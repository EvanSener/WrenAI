"""Plan a phase-one single-fact virtual Cube and render warehouse SQL."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from wren.metric_compiler import dialect_for
from wren.semantic_graph.binding_policy import enforce_master_model, master_model
from wren.semantic_graph.model import GraphPlanningError
from wren.semantic_graph.queryability import find_binding_entry


def plan_virtual_cube(
    semantic_graph: dict[str, Any],
    queryability_index: dict[str, Any],
    *,
    source_model: str,
    metrics: list[str],
    dimensions: list[str],
) -> dict[str, Any]:
    """Create a safe single-fact plan and its target-dialect SQL."""

    metrics = list(dict.fromkeys(item for item in metrics if item))
    dimensions = list(dict.fromkeys(item for item in dimensions if item))
    if not metrics:
        raise GraphPlanningError(
            "VIRTUAL_CUBE_METRIC_REQUIRED", "at least one metric is required"
        )

    nodes = {item["name"]: item for item in semantic_graph.get("nodes") or []}
    if source_model not in nodes:
        raise GraphPlanningError(
            "VIRTUAL_CUBE_SOURCE_NOT_FOUND",
            f"source model '{source_model}' is not a graph node",
        )
    metric_defs = {item["name"]: item for item in semantic_graph.get("metrics") or []}
    dimension_defs = {
        item["name"]: item for item in semantic_graph.get("dimensions") or []
    }
    edge_defs = {item["name"]: item for item in semantic_graph.get("edges") or []}

    metric_plan: list[dict[str, Any]] = []
    dimension_plan: list[dict[str, Any]] = []
    path_steps: list[tuple[int, dict[str, Any]]] = []
    rejected: list[dict[str, Any]] = []

    for metric in metrics:
        definition = metric_defs.get(metric)
        if definition is None:
            raise GraphPlanningError(
                "VIRTUAL_CUBE_METRIC_NOT_FOUND",
                f"metric '{metric}' is not defined",
            )
        enforce_master_model(
            member_kind="metric",
            member_name=metric,
            definition=definition,
            requested_model=source_model,
        )
        binding = find_binding_entry(
            queryability_index, metric=metric, source_model=source_model
        )
        if binding is None:
            raise GraphPlanningError(
                "VIRTUAL_CUBE_METRIC_NOT_BOUND",
                f"metric '{metric}' cannot be computed from source '{source_model}'",
            )
        expression = definition.get("expandedExpression")
        if not isinstance(expression, str) or not expression:
            raise GraphPlanningError(
                "VIRTUAL_CUBE_METRIC_EXPRESSION_MISSING",
                f"metric '{metric}' has no compiled expression",
            )
        metric_plan.append(
            {
                "name": metric,
                "expression": expression,
                "atomicFields": definition.get("atomicFields") or [],
                "additivity": definition.get("additivity"),
            }
        )

    primary_binding = find_binding_entry(
        queryability_index, metric=metrics[0], source_model=source_model
    )
    assert primary_binding is not None
    valid_by_dimension = {
        item["name"]: item for item in primary_binding.get("validDimensions") or []
    }
    invalid_by_dimension = {
        item["name"]: item for item in primary_binding.get("invalidDimensions") or []
    }

    for metric in metrics[1:]:
        binding = find_binding_entry(
            queryability_index, metric=metric, source_model=source_model
        )
        assert binding is not None
        valid_names = {item["name"] for item in binding.get("validDimensions") or []}
        for dimension in dimensions:
            if dimension not in valid_names:
                rejected.append(
                    {
                        "metric": metric,
                        "dimension": dimension,
                        "reason": "dimension is not valid for this metric binding",
                    }
                )

    for dimension in dimensions:
        definition = dimension_defs.get(dimension)
        if definition is None:
            raise GraphPlanningError(
                "VIRTUAL_CUBE_DIMENSION_NOT_FOUND",
                f"dimension '{dimension}' is not defined",
            )
        reachability = valid_by_dimension.get(dimension)
        if reachability is None:
            detail = invalid_by_dimension.get(dimension)
            rejected.append(
                {
                    "metric": metrics[0],
                    "dimension": dimension,
                    "reason": detail.get("reason")
                    if detail
                    else "dimension is not queryable",
                    "code": detail.get("code") if detail else "NO_SAFE_PATH",
                }
            )
            continue
        binding_model = reachability["bindingModel"]
        # The index is a generated acceleration artifact and can be stale or
        # supplied independently by an embedder. Re-enforce source-of-truth
        # member governance at planning time instead of trusting its cached
        # binding decision.
        enforce_master_model(
            member_kind="dimension",
            member_name=dimension,
            definition=definition,
            requested_model=binding_model,
        )
        configured_master = master_model(definition)
        dimension_plan.append(
            {
                "name": dimension,
                "expression": definition["expression"],
                "bindingModel": binding_model,
                "isMaster": (
                    binding_model == configured_master
                    if configured_master
                    else reachability.get("isMaster", False)
                ),
                "hops": reachability["hops"],
                "path": deepcopy(reachability["path"]),
            }
        )
        for depth, step in enumerate(reachability["path"], start=1):
            path_steps.append((depth, step))

    if rejected:
        raise GraphPlanningError(
            "VIRTUAL_CUBE_DIMENSION_NOT_QUERYABLE",
            "one or more dimensions are not safe for the requested metric binding",
            details=rejected,
        )

    joins = _merge_join_tree(source_model, path_steps, edge_defs)
    plan = {
        "schemaVersion": 1,
        "kind": "SINGLE_FACT_VIRTUAL_CUBE",
        "sourceModel": source_model,
        "sourceGrain": deepcopy(nodes[source_model].get("grain")),
        "metrics": metric_plan,
        "dimensions": dimension_plan,
        "joins": joins,
        "outputGrain": dimensions,
        "fanoutPolicy": "SAFE_MANY_TO_ONE_ONLY",
    }
    plan["sql"] = render_virtual_cube_sql(semantic_graph, plan)
    return plan


def render_virtual_cube_sql(
    semantic_graph: dict[str, Any], plan: dict[str, Any]
) -> str:
    """Render a virtual Cube plan into the project's target SQL dialect."""

    nodes = {item["name"]: item for item in semantic_graph.get("nodes") or []}
    data_source = (semantic_graph.get("project") or {}).get("dataSource")
    dialect = dialect_for(data_source)
    source_model = plan["sourceModel"]
    aliases: dict[str, str] = {source_model: "g0"}
    for join in plan.get("joins") or []:
        if join["to"] not in aliases:
            aliases[join["to"]] = f"g{len(aliases)}"

    source_sql = _relation_sql(nodes[source_model], dialect)
    from_sql = f"FROM {source_sql} AS {aliases[source_model]}"
    join_sql: list[str] = []
    for join in plan.get("joins") or []:
        target = join["to"]
        condition = _qualified_expression(
            join["condition"],
            dialect=dialect,
            default_alias=aliases[join["from"]],
            model_aliases=aliases,
        )
        join_sql.append(
            "LEFT JOIN "
            f"{_relation_sql(nodes[target], dialect)} AS {aliases[target]} "
            f"ON {condition}"
        )

    select_items: list[str] = []
    group_items: list[str] = []
    for dimension in plan.get("dimensions") or []:
        expression = _qualified_expression(
            dimension["expression"],
            dialect=dialect,
            default_alias=aliases[dimension["bindingModel"]],
            model_aliases=aliases,
        )
        group_items.append(expression)
        select_items.append(
            _alias_expression(expression, dimension["name"], dialect=dialect)
        )
    for metric in plan.get("metrics") or []:
        expression = _qualified_expression(
            metric["expression"],
            dialect=dialect,
            default_alias=aliases[source_model],
            model_aliases=aliases,
        )
        select_items.append(
            _alias_expression(expression, metric["name"], dialect=dialect)
        )

    lines = ["SELECT"]
    lines.extend(
        f"  {item}{',' if index < len(select_items) - 1 else ''}"
        for index, item in enumerate(select_items)
    )
    lines.append(from_sql)
    lines.extend(join_sql)
    if group_items:
        lines.append("GROUP BY")
        lines.extend(
            f"  {item}{',' if index < len(group_items) - 1 else ''}"
            for index, item in enumerate(group_items)
        )
    return "\n".join(lines)


def _merge_join_tree(
    source_model: str,
    path_steps: list[tuple[int, dict[str, Any]]],
    edge_defs: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    joins: list[dict[str, Any]] = []
    signatures: set[tuple[str, str, str]] = set()
    parent_by_node: dict[str, tuple[str, str]] = {}
    joined = {source_model}

    for depth, step in sorted(
        path_steps,
        key=lambda item: (
            item[0],
            item[1]["from"],
            item[1]["to"],
            item[1]["relationship"],
        ),
    ):
        relationship = step["relationship"]
        signature = (relationship, step["from"], step["to"])
        if signature in signatures:
            continue
        parent = parent_by_node.get(step["to"])
        candidate_parent = (step["from"], relationship)
        if parent is not None and parent != candidate_parent:
            raise GraphPlanningError(
                "VIRTUAL_CUBE_JOIN_TREE_AMBIGUOUS",
                f"node '{step['to']}' would be joined through both "
                f"'{parent[1]}' and '{relationship}'",
            )
        if step["from"] not in joined:
            raise GraphPlanningError(
                "VIRTUAL_CUBE_JOIN_TREE_DISCONNECTED",
                f"relationship '{relationship}' starts from unjoined node '{step['from']}'",
            )
        edge = edge_defs.get(relationship)
        if edge is None:
            raise GraphPlanningError(
                "VIRTUAL_CUBE_RELATIONSHIP_NOT_FOUND",
                f"relationship '{relationship}' is missing from semantic graph",
            )
        parent_by_node[step["to"]] = candidate_parent
        joined.add(step["to"])
        signatures.add(signature)
        joins.append(
            {
                "relationship": relationship,
                "from": step["from"],
                "to": step["to"],
                "cardinality": step["cardinality"],
                "condition": edge["condition"],
                "role": step.get("role"),
                "entity": step.get("entity"),
                "depth": depth,
            }
        )
    return joins


def _relation_sql(node: dict[str, Any], dialect: str | None) -> str:
    relation = node.get("relation") or {}
    if relation.get("type") == "table":
        reference = relation.get("tableReference") or {}
        table = reference.get("table")
        if not isinstance(table, str) or not table:
            raise GraphPlanningError(
                "VIRTUAL_CUBE_TABLE_REFERENCE_INVALID",
                f"node '{node['name']}' has no physical table name",
            )
        parts = [
            reference.get("catalog"),
            reference.get("schema"),
            table,
        ]
        return ".".join(
            _quoted_identifier(part, dialect)
            for part in parts
            if isinstance(part, str) and part
        )
    statement = relation.get("sql")
    if not isinstance(statement, str) or not statement.strip():
        raise GraphPlanningError(
            "VIRTUAL_CUBE_SQL_SOURCE_MISSING",
            f"node '{node['name']}' has no usable table or SQL source",
        )
    return f"({statement.strip().rstrip(';')})"


def _qualified_expression(
    expression: str,
    *,
    dialect: str | None,
    default_alias: str,
    model_aliases: dict[str, str],
) -> str:
    try:
        parsed = sqlglot.parse_one(expression, dialect=dialect)
    except (ParseError, ValueError) as exc:
        raise GraphPlanningError(
            "VIRTUAL_CUBE_EXPRESSION_INVALID",
            f"cannot parse expression '{expression}': {exc}",
        ) from exc
    folded_aliases = {name.casefold(): alias for name, alias in model_aliases.items()}

    def qualify(node: exp.Expression) -> exp.Expression:
        if not isinstance(node, exp.Column):
            return node
        table = node.table
        alias = folded_aliases.get(table.casefold()) if table else default_alias
        if alias is None:
            return node
        return exp.column(node.name, table=alias, quoted=node.this.args.get("quoted"))

    return parsed.transform(qualify, copy=True).sql(dialect=dialect)


def _alias_expression(expression: str, alias: str, *, dialect: str | None) -> str:
    parsed = sqlglot.parse_one(expression, dialect=dialect)
    return exp.alias_(parsed, alias, quoted=True).sql(dialect=dialect)


def _quoted_identifier(value: str, dialect: str | None) -> str:
    return exp.to_identifier(value, quoted=True).sql(dialect=dialect)

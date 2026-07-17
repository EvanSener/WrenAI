"""Metric binding, calculation validation, and additivity policy checks."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import sqlglot
from sqlglot import exp

from wren.semantic_graph.advanced_calculation_inputs import (
    resolve_calculation_inputs,
)
from wren.semantic_graph.advanced_expression import parse_calculation_expression
from wren.semantic_graph.advanced_member_routes import member_steps
from wren.semantic_graph.advanced_types import GraphState
from wren.semantic_graph.binding_policy import allowed_bindings, enforce_master_model
from wren.semantic_graph.model import GraphPlanningError


def plan_metric(
    state: GraphState, source: str, request: dict[str, Any]
) -> dict[str, Any]:
    name = request["name"]
    definition = state.metrics.get(name)
    if definition is None:
        raise GraphPlanningError(
            "GRAPH_METRIC_NOT_FOUND",
            f"metric '{name}' is not defined",
            details={"metric": name},
        )
    enforce_master_model(
        member_kind="metric",
        member_name=name,
        definition=definition,
        requested_model=source,
    )
    binding = next(
        iter(
            allowed_bindings(
                definition,
                (
                    item
                    for item in state.metric_bindings
                    if item.get("metric") == name and item.get("model") == source
                ),
            )
        ),
        None,
    )
    if binding is None:
        raise GraphPlanningError(
            "GRAPH_METRIC_NOT_BOUND",
            f"metric '{name}' cannot be computed from fact '{source}'",
            details={"metric": name, "sourceModel": source},
        )
    expression = definition.get("expandedExpression")
    if not isinstance(expression, str) or not expression.strip():
        raise GraphPlanningError(
            "GRAPH_METRIC_EXPRESSION_MISSING",
            f"metric '{name}' has no compiled expression",
        )
    blocked = definition.get("blockedDimensions") or []
    if not isinstance(blocked, list) or not all(
        isinstance(item, str) and item for item in blocked
    ):
        raise GraphPlanningError(
            "GRAPH_METRIC_POLICY_INVALID",
            f"metric '{name}' has an invalid blockedDimensions policy",
            details={"metric": name, "blockedDimensions": blocked},
        )
    return {
        "name": name,
        "alias": request["alias"],
        "expression": expression,
        "atomicFields": list(definition.get("atomicFields") or []),
        "binding": binding.get("id"),
        "bindingModel": source,
        "isMaster": bool(binding.get("isMaster")),
        "masterModel": definition.get("masterModel"),
        "additivity": str(definition.get("additivity") or "unknown").lower(),
        "blockedDimensions": blocked,
        "additivitySource": definition.get("additivitySource"),
    }


def assign_metric_calculations(
    state: GraphState,
    adjacency: dict[str, list[dict[str, Any]]],
    facts: list[dict[str, Any]],
    calculations: list[dict[str, Any]],
    discovery: dict[str, Any],
    *,
    max_depth: int,
) -> None:
    for calculation in calculations:
        requested_source = calculation.get("sourceModel")
        candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
        failures: list[dict[str, Any]] = []
        for fact in facts:
            source = fact["sourceModel"]
            if requested_source and source != requested_source:
                continue
            try:
                compiled = compile_metric_calculation(
                    state,
                    calculation,
                    source,
                    adjacency=adjacency,
                    max_depth=max_depth,
                )
            except GraphPlanningError as exc:
                failures.append(
                    {
                        "sourceModel": source,
                        "code": exc.code,
                        "reason": str(exc),
                        "details": deepcopy(exc.details),
                    }
                )
                continue
            candidates.append((fact, compiled))
        if not candidates:
            raise GraphPlanningError(
                "GRAPH_METRIC_CALCULATION_UNSAFE",
                f"metric calculation '{calculation['name']}' cannot bind to a selected fact",
                details={
                    "calculation": calculation["name"],
                    "requestedSource": requested_source,
                    "failures": failures,
                },
            )
        if len(candidates) > 1:
            raise GraphPlanningError(
                "GRAPH_METRIC_CALCULATION_BINDING_AMBIGUOUS",
                f"metric calculation '{calculation['name']}' is valid on multiple facts",
                details={
                    "calculation": calculation["name"],
                    "sourceModels": [fact["sourceModel"] for fact, _ in candidates],
                    "hint": "set calculations[].sourceModel",
                },
            )
        fact, compiled = candidates[0]
        fact["metrics"].append(
            {
                "name": calculation["name"],
                "alias": calculation["alias"],
                "_compiledMetric": compiled,
            }
        )
        discovery["acceptedMembers"].append(
            {
                "kind": "calculation",
                "calculationKind": "metric",
                "name": calculation["name"],
                "alias": calculation["alias"],
                "bindingModel": fact["sourceModel"],
                "reason": "SQLGlot-validated aggregate calculation",
            }
        )


def compile_metric_calculation(
    state: GraphState,
    calculation: dict[str, Any],
    source: str,
    *,
    adjacency: dict[str, list[dict[str, Any]]] | None = None,
    max_depth: int = 0,
) -> dict[str, Any]:
    expression = calculation["expression"]
    parsed = parse_calculation_expression(
        expression,
        dialect=state.dialect,
        name=calculation["name"],
    )

    referenced_metrics: set[str] = set()

    def expand_metric(node: exp.Expression) -> exp.Expression:
        if not isinstance(node, exp.Column) or node.table:
            return node
        definition = state.metrics.get(node.name)
        if definition is None:
            return node
        enforce_master_model(
            member_kind="metric",
            member_name=node.name,
            definition=definition,
            requested_model=source,
        )
        if not allowed_bindings(
            definition,
            (
                binding
                for binding in state.metric_bindings
                if binding.get("metric") == node.name and binding.get("model") == source
            ),
        ):
            raise GraphPlanningError(
                "GRAPH_CALCULATION_METRIC_NOT_BOUND",
                f"global metric '{node.name}' is not bound to '{source}'",
                details={"metric": node.name, "sourceModel": source},
            )
        expanded = definition.get("expandedExpression")
        if not isinstance(expanded, str) or not expanded:
            raise GraphPlanningError(
                "GRAPH_CALCULATION_METRIC_INVALID",
                f"global metric '{node.name}' has no expanded expression",
            )
        referenced_metrics.add(node.name)
        return exp.Paren(this=sqlglot.parse_one(expanded, dialect=state.dialect))

    parsed = parsed.transform(expand_metric, copy=True)
    if parsed.find(exp.AggFunc) is None:
        raise GraphPlanningError(
            "GRAPH_METRIC_CALCULATION_AGGREGATE_REQUIRED",
            f"metric calculation '{calculation['name']}' must contain an aggregate or global metric",
            details={"expression": expression},
        )
    input_plan: dict[str, Any] | None = None
    if "inputs" in calculation:
        if adjacency is None:
            raise GraphPlanningError(
                "GRAPH_CALCULATION_INPUT_CONTEXT_REQUIRED",
                f"metric calculation '{calculation['name']}' needs graph paths",
            )
        input_plan = resolve_calculation_inputs(
            state,
            adjacency,
            source=source,
            request=calculation,
            parsed=parsed,
            max_depth=max_depth,
        )
        unsafe_steps = [
            step for step in member_steps(input_plan) if step["traversal"] != "SAFE"
        ]
        if unsafe_steps:
            raise GraphPlanningError(
                "GRAPH_REMOTE_METRIC_FANOUT_UNSAFE",
                f"metric calculation '{calculation['name']}' reads a leaf field "
                "through fanout or many-to-many traversal",
                details={
                    "calculation": calculation["name"],
                    "relationships": sorted(
                        {step["relationship"] for step in unsafe_steps}
                    ),
                    "hint": (
                        "model the remote node as its own fact, or provide a "
                        "governed allocation before aggregating its fields"
                    ),
                },
            )
        available_inputs = {
            (item["model"].casefold(), item["field"].casefold())
            for item in input_plan["inputs"]
        }
    else:
        available = {
            attribute["name"].casefold()
            for attribute in state.nodes[source].get("attributes") or []
            if isinstance(attribute, dict) and isinstance(attribute.get("name"), str)
        }
    invalid_columns: list[str] = []
    unaggregated_columns: list[str] = []
    for column in parsed.find_all(exp.Column):
        if input_plan is not None:
            if (
                not column.table
                or (column.table.casefold(), column.name.casefold())
                not in available_inputs
            ):
                invalid_columns.append(column.sql(dialect=state.dialect))
                continue
        else:
            if column.table and column.table.casefold() != source.casefold():
                invalid_columns.append(column.sql(dialect=state.dialect))
                continue
            if column.name.casefold() not in available:
                invalid_columns.append(column.sql(dialect=state.dialect))
                continue
        ancestor = column.parent
        while ancestor is not None and not isinstance(ancestor, exp.AggFunc):
            ancestor = ancestor.parent
        if ancestor is None:
            unaggregated_columns.append(column.sql(dialect=state.dialect))
    if invalid_columns:
        raise GraphPlanningError(
            "GRAPH_CALCULATION_FIELD_UNREACHABLE",
            f"metric calculation '{calculation['name']}' references fields outside fact '{source}'",
            details={"invalidFields": sorted(set(invalid_columns))},
        )
    if unaggregated_columns:
        raise GraphPlanningError(
            "GRAPH_METRIC_CALCULATION_FIELD_NOT_AGGREGATED",
            f"metric calculation '{calculation['name']}' contains unaggregated fields",
            details={"fields": sorted(set(unaggregated_columns))},
        )
    additivity = "non_additive"
    if isinstance(parsed, (exp.Sum, exp.Min, exp.Max)):
        additivity = "additive"
    elif isinstance(parsed, exp.Count) and parsed.find(exp.Distinct) is None:
        additivity = "additive"
    compiled = {
        "name": calculation["name"],
        "alias": calculation["alias"],
        "expression": parsed.sql(dialect=state.dialect),
        "atomicFields": sorted({column.name for column in parsed.find_all(exp.Column)}),
        "binding": f"calculation:{calculation['name']}@{source}",
        "additivity": additivity,
        "blockedDimensions": [],
        "additivitySource": "calculation",
        "referencedMetrics": sorted(referenced_metrics),
        "memberKind": "calculation",
    }
    if input_plan is not None:
        compiled.update(input_plan)
        compiled["defaultModel"] = source
    return compiled


def validate_metric_policies(
    metrics: list[dict[str, Any]],
    dimensions: list[dict[str, Any]],
    *,
    fanout: bool,
) -> None:
    requested = {dimension["name"] for dimension in dimensions}
    requested_time = {
        dimension["name"]
        for dimension in dimensions
        if any(
            token in str(dimension.get("type") or "").casefold()
            for token in ("date", "time", "timestamp")
        )
    }
    violations: list[dict[str, Any]] = []
    for metric in metrics:
        blocked = set(metric["blockedDimensions"])
        matching = sorted(requested & blocked)
        if "*" in blocked or "__all__" in blocked:
            matching = sorted(requested)
        if blocked & {"time", "time_dimensions", "__time__"}:
            matching = sorted(set(matching) | requested_time)
        if matching:
            violations.append(
                {
                    "metric": metric["name"],
                    "code": "GRAPH_METRIC_DIMENSION_BLOCKED",
                    "dimensions": matching,
                    "additivity": metric["additivity"],
                    "additivitySource": metric.get("additivitySource"),
                }
            )
        if fanout and metric["additivity"] in {"non_additive", "unknown"}:
            violations.append(
                {
                    "metric": metric["name"],
                    "code": "GRAPH_METRIC_NOT_ADDITIVE_FOR_FANOUT",
                    "dimensions": sorted(requested),
                    "additivity": metric["additivity"],
                    "additivitySource": metric.get("additivitySource"),
                }
            )
    if violations:
        raise GraphPlanningError(
            violations[0]["code"],
            "one or more metric additivity policies reject the requested grain",
            details={"violations": violations},
        )

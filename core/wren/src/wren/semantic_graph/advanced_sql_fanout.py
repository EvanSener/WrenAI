"""Render preaggregate, deduplicated mapping, and allocated fanout stages."""

from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from wren.semantic_graph.advanced_sql_common import column_sql, select_sql
from wren.semantic_graph.advanced_sql_routes import (
    render_fact_relation,
    render_routes,
)
from wren.semantic_graph.advanced_types import GraphState
from wren.semantic_graph.model import GraphPlanningError
from wren.semantic_graph.planner import (
    _alias_expression,
    _qualified_expression,
    _quoted_identifier,
)


def render_fanout_fact_ctes(
    state: GraphState, fact: dict[str, Any]
) -> list[tuple[str, str]]:
    prefix = fact["id"]
    source_alias = "s"
    relation = render_fact_relation(state, fact, fact["sourceModel"])
    key_aliases = [f"__fact_key_{index}" for index in range(len(fact["sourceKeys"]))]
    pre_select: list[str] = []
    pre_group: list[str] = []
    for field, alias in zip(fact["sourceKeys"], key_aliases, strict=True):
        expression = column_sql(source_alias, field, state.dialect)
        pre_group.append(expression)
        pre_select.append(_alias_expression(expression, alias, dialect=state.dialect))
    for metric in fact["metrics"]:
        expression = _qualified_expression(
            metric["expression"],
            dialect=state.dialect,
            default_alias=source_alias,
            model_aliases={fact["sourceModel"]: source_alias},
        )
        pre_select.append(
            _alias_expression(expression, metric["alias"], dialect=state.dialect)
        )
    preaggregate = select_sql(
        pre_select, f"FROM {relation} AS {source_alias}", [], pre_group
    )

    from_sql, joins, member_aliases, allocations = render_routes(state, fact)
    if len(allocations) > 1:
        raise GraphPlanningError(
            "GRAPH_MULTIPLE_ALLOCATION_PATHS_UNSUPPORTED",
            "one fact query cannot combine independent bridge allocations",
            details={"allocations": allocations},
        )
    grain_select: list[tuple[str, str]] = []
    for entity in fact["entityGrain"]:
        for field, output_alias in zip(
            entity["fields"], entity["outputAliases"], strict=True
        ):
            grain_select.append(
                (column_sql(source_alias, field, state.dialect), output_alias)
            )
    for dimension in fact["dimensions"]:
        model_aliases = member_aliases[dimension["alias"]]
        default_model = dimension.get("defaultModel") or dimension.get("bindingModel")
        expression = _qualified_expression(
            dimension["expression"],
            dialect=state.dialect,
            default_alias=model_aliases.get(default_model, source_alias),
            model_aliases=model_aliases,
        )
        grain_select.append((expression, dimension["alias"]))

    mapping_items: list[str] = []
    for field, alias in zip(fact["sourceKeys"], key_aliases, strict=True):
        mapping_items.append(
            _alias_expression(
                column_sql(source_alias, field, state.dialect),
                alias,
                dialect=state.dialect,
            )
        )
    mapping_items.extend(
        _alias_expression(expression, alias, dialect=state.dialect)
        for expression, alias in grain_select
    )
    if allocations:
        mapping_items.append(
            _alias_expression(
                allocations[0]["expression"],
                "__allocation_weight",
                dialect=state.dialect,
            )
        )
    mapping_raw = select_sql(mapping_items, from_sql, joins, [])

    mapping_columns = [*key_aliases, *(alias for _, alias in grain_select)]
    raw_alias = "mr"
    raw_columns = [
        column_sql(raw_alias, column, state.dialect) for column in mapping_columns
    ]
    mapping_select = [
        _alias_expression(column, alias, dialect=state.dialect)
        for column, alias in zip(raw_columns, mapping_columns, strict=True)
    ]
    if allocations:
        weight = column_sql(raw_alias, "__allocation_weight", state.dialect)
        weight_sum = exp.Sum(this=sqlglot.parse_one(weight, dialect=state.dialect)).sql(
            dialect=state.dialect
        )
        mapping_select.append(
            _alias_expression(weight_sum, "__allocation_weight", dialect=state.dialect)
        )
    mapping = select_sql(
        mapping_select,
        f"FROM {_quoted_identifier(prefix + '_mapping_raw', state.dialect)} AS {raw_alias}",
        [],
        raw_columns,
    )

    pre_alias = "p"
    map_alias = "m"
    join_conditions = [
        f"{column_sql(pre_alias, alias, state.dialect)} = "
        f"{column_sql(map_alias, alias, state.dialect)}"
        for alias in key_aliases
    ]
    final_grain = [
        column_sql(map_alias, alias, state.dialect) for _, alias in grain_select
    ]
    final_select = [
        _alias_expression(expression, alias, dialect=state.dialect)
        for expression, (_, alias) in zip(final_grain, grain_select, strict=True)
    ]
    allocation_weight = (
        column_sql(map_alias, "__allocation_weight", state.dialect)
        if allocations
        else None
    )
    for metric in fact["metrics"]:
        value = column_sql(pre_alias, metric["alias"], state.dialect)
        rolled = rollup_expression(
            state, metric, value, allocation_weight=allocation_weight
        )
        final_select.append(
            _alias_expression(rolled, metric["alias"], dialect=state.dialect)
        )
    final_body = select_sql(
        final_select,
        f"FROM {_quoted_identifier(prefix + '_preaggregate', state.dialect)} AS {pre_alias}",
        [
            "LEFT JOIN "
            f"{_quoted_identifier(prefix + '_mapping', state.dialect)} AS {map_alias} "
            f"ON {' AND '.join(join_conditions)}"
        ],
        final_grain,
    )
    return [
        (f"{prefix}_preaggregate", preaggregate),
        (f"{prefix}_mapping_raw", mapping_raw),
        (f"{prefix}_mapping", mapping),
        (prefix, final_body),
    ]


def rollup_expression(
    state: GraphState,
    metric: dict[str, Any],
    value: str,
    *,
    allocation_weight: str | None,
) -> str:
    try:
        parsed = sqlglot.parse_one(metric["expression"], dialect=state.dialect)
    except (ParseError, ValueError) as exc:
        raise GraphPlanningError(
            "GRAPH_METRIC_EXPRESSION_INVALID",
            f"cannot parse metric '{metric['name']}' for fanout rollup: {exc}",
        ) from exc
    if isinstance(parsed, exp.Min):
        operator = "MIN"
    elif isinstance(parsed, exp.Max):
        operator = "MAX"
    else:
        operator = "SUM"
    if allocation_weight is not None and operator != "SUM":
        raise GraphPlanningError(
            "GRAPH_ALLOCATION_ROLLUP_UNSUPPORTED",
            f"metric '{metric['name']}' cannot apply allocation with {operator} rollup",
            details={"metric": metric["name"], "rollup": operator},
        )
    value_expression = sqlglot.parse_one(value, dialect=state.dialect)
    if allocation_weight is not None:
        weight = sqlglot.parse_one(allocation_weight, dialect=state.dialect)
        value_expression = exp.Mul(
            this=value_expression,
            expression=exp.Coalesce(this=weight, expressions=[exp.Literal.number(0)]),
        )
    if operator == "MIN":
        aggregate: exp.Expression = exp.Min(this=value_expression)
    elif operator == "MAX":
        aggregate = exp.Max(this=value_expression)
    else:
        aggregate = exp.Sum(this=value_expression)
    return aggregate.sql(dialect=state.dialect)

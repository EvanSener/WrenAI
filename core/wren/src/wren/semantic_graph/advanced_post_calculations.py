"""Validate and render calculations over already aggregated graph outputs."""

from __future__ import annotations

from typing import Any

from sqlglot import exp

from wren.semantic_graph.advanced_expression import parse_calculation_expression
from wren.semantic_graph.advanced_sql_common import column_sql, select_sql
from wren.semantic_graph.advanced_types import GraphState
from wren.semantic_graph.model import GraphPlanningError
from wren.semantic_graph.planner import (
    _alias_expression,
    _qualified_expression,
    _quoted_identifier,
)


def compile_post_calculations(
    calculations: list[dict[str, Any]],
    *,
    available_columns: list[str],
    dialect: str | None,
) -> list[dict[str, Any]]:
    """Compile scalar/window expressions over fact or multi-fact outputs."""

    available = {name.casefold(): name for name in available_columns}
    aliases = set(available)
    compiled: list[dict[str, Any]] = []
    for calculation in calculations:
        name = calculation["name"]
        alias = calculation["alias"]
        if alias.casefold() in aliases:
            raise GraphPlanningError(
                "GRAPH_OUTPUT_ALIAS_CONFLICT",
                f"post-aggregate calculation alias '{alias}' is already in use",
                details={"alias": alias, "calculation": name},
            )
        parsed = parse_calculation_expression(
            calculation["expression"], dialect=dialect, name=name
        )

        invalid: set[str] = set()
        referenced: set[str] = set()
        for column in parsed.find_all(exp.Column):
            if column.table or column.name.casefold() not in available:
                invalid.add(column.sql(dialect=dialect))
            else:
                referenced.add(available[column.name.casefold()])
        if invalid:
            raise GraphPlanningError(
                "GRAPH_POST_CALCULATION_FIELD_UNAVAILABLE",
                f"post-aggregate calculation '{name}' references unavailable outputs",
                details={
                    "calculation": name,
                    "fields": sorted(invalid),
                    "availableColumns": available_columns,
                },
            )
        unwindowed = [
            aggregate.sql(dialect=dialect)
            for aggregate in parsed.find_all(exp.AggFunc)
            if aggregate.find_ancestor(exp.Window) is None
        ]
        if unwindowed:
            raise GraphPlanningError(
                "GRAPH_POST_CALCULATION_REAGGREGATION_FORBIDDEN",
                f"post-aggregate calculation '{name}' cannot aggregate the output again",
                details={"aggregates": sorted(set(unwindowed))},
            )
        compiled.append(
            {
                "name": name,
                "alias": alias,
                "kind": "post_metric",
                "stage": "post_aggregate",
                "expression": parsed.sql(dialect=dialect),
                "referencedColumns": sorted(referenced),
            }
        )
        aliases.add(alias.casefold())
    return compiled


def render_post_aggregate(
    state: GraphState,
    *,
    relation: str,
    input_columns: list[str],
    calculations: list[dict[str, Any]],
) -> str:
    """Project aggregate outputs plus validated post-aggregate expressions."""

    relation_alias = "q"
    select_items = [
        _alias_expression(
            column_sql(relation_alias, column, state.dialect),
            column,
            dialect=state.dialect,
        )
        for column in input_columns
    ]
    for calculation in calculations:
        expression = _qualified_expression(
            calculation["expression"],
            dialect=state.dialect,
            default_alias=relation_alias,
            model_aliases={},
        )
        select_items.append(
            _alias_expression(
                expression,
                calculation["alias"],
                dialect=state.dialect,
            )
        )
    return select_sql(
        select_items,
        f"FROM {_quoted_identifier(relation, state.dialect)} AS {relation_alias}",
        [],
        [],
    )

"""Compose per-fact SQL, multi-fact merge CTEs, and the final projection."""

from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp

from wren.semantic_graph.advanced_sql_common import column_sql, select_sql
from wren.semantic_graph.advanced_sql_fanout import render_fanout_fact_ctes
from wren.semantic_graph.advanced_sql_routes import render_direct_fact_body
from wren.semantic_graph.advanced_types import GraphState
from wren.semantic_graph.planner import _alias_expression, _quoted_identifier


def render_fact_ctes(state: GraphState, fact: dict[str, Any]) -> list[tuple[str, str]]:
    if fact["strategy"] == "DIRECT_AGGREGATE":
        return [(fact["id"], render_direct_fact_body(state, fact))]
    return render_fanout_fact_ctes(state, fact)


def render_multi_fact_merge(
    state: GraphState,
    facts: list[dict[str, Any]],
    *,
    grain_aliases: list[str],
) -> tuple[list[tuple[str, str]], str, dict[str, Any]]:
    ctes: list[tuple[str, str]] = []
    left_relation = facts[0]["outputRelation"]
    left_metrics = [metric["alias"] for metric in facts[0]["metrics"]]
    steps: list[dict[str, Any]] = []
    for index, fact in enumerate(facts[1:], start=1):
        right_relation = fact["outputRelation"]
        right_metrics = [metric["alias"] for metric in fact["metrics"]]
        merge_relation = f"merge_{index}"
        left_alias = "l"
        right_alias = "r"
        select_items: list[str] = []
        conditions: list[str] = []
        for grain in grain_aliases:
            left = column_sql(left_alias, grain, state.dialect)
            right = column_sql(right_alias, grain, state.dialect)
            coalesced = exp.Coalesce(
                this=sqlglot.parse_one(left, dialect=state.dialect),
                expressions=[sqlglot.parse_one(right, dialect=state.dialect)],
            ).sql(dialect=state.dialect)
            select_items.append(
                _alias_expression(coalesced, grain, dialect=state.dialect)
            )
            conditions.append(f"{left} <=> {right}")
        for alias in left_metrics:
            select_items.append(
                _alias_expression(
                    column_sql(left_alias, alias, state.dialect),
                    alias,
                    dialect=state.dialect,
                )
            )
        for alias in right_metrics:
            select_items.append(
                _alias_expression(
                    column_sql(right_alias, alias, state.dialect),
                    alias,
                    dialect=state.dialect,
                )
            )
        body = select_sql(
            select_items,
            f"FROM {_quoted_identifier(left_relation, state.dialect)} AS {left_alias}",
            [
                "FULL OUTER JOIN "
                f"{_quoted_identifier(right_relation, state.dialect)} AS {right_alias} "
                f"ON {' AND '.join(conditions) if conditions else '1 = 1'}"
            ],
            [],
        )
        ctes.append((merge_relation, body))
        steps.append(
            {
                "left": left_relation,
                "right": right_relation,
                "output": merge_relation,
                "joinType": "FULL_OUTER",
                "grainColumns": grain_aliases,
            }
        )
        left_relation = merge_relation
        left_metrics.extend(right_metrics)
    return (
        ctes,
        left_relation,
        {
            "strategy": "AGGREGATE_THEN_FULL_OUTER_JOIN",
            "grainColumns": grain_aliases,
            "steps": steps,
        },
    )


def render_final_select(state: GraphState, *, relation: str, columns: list[str]) -> str:
    lines = ["SELECT"]
    lines.extend(
        f"  {_quoted_identifier(column, state.dialect)}"
        f"{',' if index < len(columns) - 1 else ''}"
        for index, column in enumerate(columns)
    )
    lines.append(f"FROM {_quoted_identifier(relation, state.dialect)}")
    return "\n".join(lines)


def render_with_query(
    state: GraphState, ctes: list[tuple[str, str]], final_sql: str
) -> str:
    rendered = []
    for name, body in ctes:
        indented = "\n".join(f"  {line}" for line in body.splitlines())
        rendered.append(
            f"{_quoted_identifier(name, state.dialect)} AS (\n{indented}\n)"
        )
    return "WITH\n" + ",\n".join(rendered) + "\n" + final_sql


def output_grain_aliases(fact: dict[str, Any]) -> list[str]:
    aliases = [
        output_alias
        for entity in fact["entityGrain"]
        for output_alias in entity["outputAliases"]
    ]
    aliases.extend(dimension["alias"] for dimension in fact["dimensions"])
    return aliases

"""Render model routes and direct per-fact aggregates."""

from __future__ import annotations

from typing import Any

from wren.semantic_graph.advanced_bridge import normalized_bridge_policy
from wren.semantic_graph.advanced_member_routes import member_routes
from wren.semantic_graph.advanced_sql_common import column_sql, select_sql
from wren.semantic_graph.advanced_types import GraphState
from wren.semantic_graph.partition import render_partitioned_relation
from wren.semantic_graph.planner import (
    _alias_expression,
    _qualified_expression,
    _relation_sql,
)


def render_direct_fact_body(state: GraphState, fact: dict[str, Any]) -> str:
    from_sql, joins, member_aliases, _ = render_routes(state, fact)
    source_alias = "s"
    select_items: list[str] = []
    group_items: list[str] = []
    for entity in fact["entityGrain"]:
        for field, output_alias in zip(
            entity["fields"], entity["outputAliases"], strict=True
        ):
            expression = column_sql(source_alias, field, state.dialect)
            group_items.append(expression)
            select_items.append(
                _alias_expression(expression, output_alias, dialect=state.dialect)
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
        group_items.append(expression)
        select_items.append(
            _alias_expression(expression, dimension["alias"], dialect=state.dialect)
        )
    for metric in fact["metrics"]:
        model_aliases = member_aliases.get(
            metric["alias"], {fact["sourceModel"]: source_alias}
        )
        expression = _qualified_expression(
            metric["expression"],
            dialect=state.dialect,
            default_alias=model_aliases.get(
                metric.get("defaultModel") or fact["sourceModel"], source_alias
            ),
            model_aliases=model_aliases,
        )
        select_items.append(
            _alias_expression(expression, metric["alias"], dialect=state.dialect)
        )
    return select_sql(select_items, from_sql, joins, group_items)


def render_routes(
    state: GraphState, fact: dict[str, Any]
) -> tuple[str, list[str], dict[str, dict[str, str]], list[dict[str, Any]]]:
    source = fact["sourceModel"]
    aliases: dict[tuple[tuple[str, str, str], ...], str] = {(): "s"}
    joins: list[str] = []
    routable_members = [
        *fact["dimensions"],
        *(metric for metric in fact["metrics"] if metric.get("routes")),
    ]
    member_aliases: dict[str, dict[str, str]] = {
        member["alias"]: {source: "s"} for member in routable_members
    }
    allocations: dict[tuple[tuple[str, str, str], ...], dict[str, Any]] = {}
    alias_counter = 0
    routed_members = [
        (member, route)
        for member in routable_members
        for route in member_routes(member)
    ]
    for member, member_route in sorted(
        routed_members,
        key=lambda item: (
            len(item[1].get("path") or []),
            [step["relationship"] for step in item[1].get("path") or []],
            item[0]["alias"],
            item[1].get("model") or "",
        ),
    ):
        prefix: tuple[tuple[str, str, str], ...] = ()
        current_alias = "s"
        for step in member_route.get("path") or []:
            signature = (step["relationship"], step["from"], step["to"])
            route = (*prefix, signature)
            if route in aliases:
                current_alias = aliases[route]
                prefix = route
                continue
            edge = state.edges[step["relationship"]]
            if step["traversal"] == "BRIDGE":
                policy = normalized_bridge_policy(state, step)
                bridge_alias = f"j{alias_counter}"
                alias_counter += 1
                target_alias = f"j{alias_counter}"
                alias_counter += 1
                source_edge = state.edges[policy["sourceRelationship"]]
                target_edge = state.edges[policy["targetRelationship"]]
                source_condition = _qualified_expression(
                    source_edge["condition"],
                    dialect=state.dialect,
                    default_alias=current_alias,
                    model_aliases={
                        step["from"]: current_alias,
                        policy["model"]: bridge_alias,
                    },
                )
                target_condition = _qualified_expression(
                    target_edge["condition"],
                    dialect=state.dialect,
                    default_alias=bridge_alias,
                    model_aliases={
                        policy["model"]: bridge_alias,
                        step["to"]: target_alias,
                    },
                )
                bridge_relation = render_fact_relation(state, fact, policy["model"])
                joins.append(
                    "LEFT JOIN "
                    f"{bridge_relation} "
                    f"AS {bridge_alias} ON {source_condition}"
                )
                target_relation = render_fact_relation(state, fact, step["to"])
                joins.append(
                    "LEFT JOIN "
                    f"{target_relation} "
                    f"AS {target_alias} ON {target_condition}"
                )
                allocation_expression = _qualified_expression(
                    policy["allocationExpression"],
                    dialect=state.dialect,
                    default_alias=bridge_alias,
                    model_aliases={
                        step["from"]: current_alias,
                        policy["model"]: bridge_alias,
                        step["to"]: target_alias,
                    },
                )
                allocations[route] = {
                    "relationship": step["relationship"],
                    "bridgeModel": policy["model"],
                    "allocationMode": policy["allocationMode"],
                    "allocationExpression": policy["allocationExpression"],
                    "expression": allocation_expression,
                }
                current_alias = target_alias
            else:
                target_alias = f"j{alias_counter}"
                alias_counter += 1
                condition = _qualified_expression(
                    edge["condition"],
                    dialect=state.dialect,
                    default_alias=current_alias,
                    model_aliases={
                        step["from"]: current_alias,
                        step["to"]: target_alias,
                    },
                )
                target_relation = render_fact_relation(state, fact, step["to"])
                joins.append(
                    f"LEFT JOIN {target_relation} AS {target_alias} ON {condition}"
                )
                current_alias = target_alias
            aliases[route] = current_alias
            prefix = route
        model = member_route.get("model")
        if isinstance(model, str):
            member_aliases[member["alias"]][model] = current_alias
    for member in routable_members:
        if member.get("memberKind") == "calculation":
            member["modelAliases"] = dict(member_aliases[member["alias"]])
    source_relation = render_fact_relation(state, fact, source)
    return (
        f"FROM {source_relation} AS s",
        joins,
        member_aliases,
        list(allocations.values()),
    )


def render_fact_relation(state: GraphState, fact: dict[str, Any], model: str) -> str:
    """Render one relation with the fact plan's model-aware partition policy."""

    node = state.nodes[model]
    relation = _relation_sql(node, state.dialect)
    plan = (fact.get("relationPartitions") or {}).get(model)
    return render_partitioned_relation(
        relation,
        node,
        plan,
        dialect=state.dialect,
    )

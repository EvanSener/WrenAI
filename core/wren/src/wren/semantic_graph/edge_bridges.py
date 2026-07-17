"""Validate Bridge/Allocation policies for compiled M:N graph edges."""

from __future__ import annotations

from typing import Any

from sqlglot import exp

from wren.semantic_graph.advanced_expression import parse_calculation_expression
from wren.semantic_graph.config import RELATIONSHIP_FILE
from wren.semantic_graph.model import GraphConfig, GraphIssue, GraphPlanningError


def validate_bridge_policies(
    records: list[dict[str, Any]],
    nodes: dict[str, dict[str, Any]],
    config: GraphConfig,
    dialect: str | None,
    issues: list[GraphIssue],
) -> None:
    by_name = {record["name"]: record for record in records}
    for relationship, policy in sorted(config.bridge_policies.items()):
        path = f"{RELATIONSHIP_FILE} > graph > bridges > {relationship}"
        edge = by_name.get(relationship)
        if edge is None:
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_BRIDGE_RELATIONSHIP_UNKNOWN",
                    path,
                    "bridge policy must reference an existing relationship",
                )
            )
            continue
        if edge["cardinality"] != "MANY_TO_MANY":
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_BRIDGE_RELATIONSHIP_NOT_MANY_TO_MANY",
                    path,
                    "bridge policy can only govern a MANY_TO_MANY relationship",
                )
            )
            continue

        bridge = policy["model"]
        if bridge not in nodes:
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_BRIDGE_MODEL_UNKNOWN",
                    f"{path} > model",
                    f"bridge model '{bridge}' is not a graph node",
                )
            )
            continue

        source, target = edge["declaredModels"]
        source_edge = by_name.get(policy["source_relationship"])
        target_edge = by_name.get(policy["target_relationship"])
        valid = True
        for label, related_edge, endpoint in (
            ("source_relationship", source_edge, source),
            ("target_relationship", target_edge, target),
        ):
            relation_name = policy[label]
            if related_edge is None:
                issues.append(
                    GraphIssue(
                        "error",
                        "GRAPH_BRIDGE_EDGE_UNKNOWN",
                        f"{path} > {label}",
                        f"relationship '{relation_name}' does not exist",
                    )
                )
                valid = False
                continue
            expected_pair = {bridge, endpoint}
            if set(related_edge["declaredModels"]) != expected_pair:
                issues.append(
                    GraphIssue(
                        "error",
                        "GRAPH_BRIDGE_EDGE_ENDPOINT_MISMATCH",
                        f"{path} > {label}",
                        f"relationship '{relation_name}' must connect '{bridge}' and '{endpoint}'",
                    )
                )
                valid = False
                continue
            if [bridge, endpoint] not in related_edge.get("safeDirections", []):
                issues.append(
                    GraphIssue(
                        "error",
                        "GRAPH_BRIDGE_EDGE_NOT_SAFE",
                        f"{path} > {label}",
                        f"relationship '{relation_name}' must expose a verified direction from bridge to endpoint",
                    )
                )
                valid = False

        expression = policy["allocation_expression"]
        try:
            parsed = parse_calculation_expression(
                expression,
                dialect=dialect,
                name=f"bridge allocation {relationship}",
            )
        except GraphPlanningError as exc:
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_BRIDGE_ALLOCATION_INVALID",
                    f"{path} > allocation_expression",
                    f"cannot parse allocation expression: {exc}",
                )
            )
            valid = False
        else:
            if parsed.find(exp.AggFunc) is not None:
                issues.append(
                    GraphIssue(
                        "error",
                        "GRAPH_BRIDGE_ALLOCATION_AGGREGATE_UNSUPPORTED",
                        f"{path} > allocation_expression",
                        "allocation expression must be a row-level bridge expression",
                    )
                )
                valid = False
            available = {field.casefold() for field in nodes[bridge]["field_names"]}
            for column in parsed.find_all(exp.Column):
                if column.table and column.table.casefold() != bridge.casefold():
                    issues.append(
                        GraphIssue(
                            "error",
                            "GRAPH_BRIDGE_ALLOCATION_QUALIFIER_INVALID",
                            f"{path} > allocation_expression",
                            f"allocation column '{column.sql(dialect=dialect)}' must reference bridge '{bridge}'",
                        )
                    )
                    valid = False
                if column.name.casefold() not in available:
                    issues.append(
                        GraphIssue(
                            "error",
                            "GRAPH_BRIDGE_ALLOCATION_FIELD_MISSING",
                            f"{path} > allocation_expression",
                            f"bridge model '{bridge}' does not expose allocation field '{column.name}'",
                        )
                    )
                    valid = False

        if not valid:
            continue
        edge["bridgePolicy"] = {
            "model": bridge,
            "sourceRelationship": policy["source_relationship"],
            "targetRelationship": policy["target_relationship"],
            "allocationExpression": expression,
            "allocationMode": policy["allocation_mode"],
        }
        edge["cardinalityValidation"] = "bridge_verified"

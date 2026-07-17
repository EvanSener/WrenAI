"""Compile model and view metadata into semantic graph nodes."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any

from wren.metric_compiler import ObjectFieldResolver
from wren.semantic_graph.model import GraphIssue


def compile_nodes(
    models: list[dict],
    views: list[dict],
    dialect: str | None,
    issues: list[GraphIssue],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Compile models and views while retaining field state for later bindings."""

    resolver = ObjectFieldResolver(models, views, dialect)
    nodes: list[dict[str, Any]] = []
    state: dict[str, dict[str, Any]] = {}

    for kind, objects in (("model", models), ("view", views)):
        for index, obj in enumerate(objects):
            name = obj.get("name")
            source = obj.get("_source_dir", f"{kind}s[{index}]")
            path = f"{kind}s/{source}"
            if not isinstance(name, str) or not name:
                issues.append(
                    GraphIssue(
                        "error",
                        "GRAPH_NODE_NAME_MISSING",
                        path,
                        f"{kind} must define a name",
                    )
                )
                continue
            if name in state:
                issues.append(
                    GraphIssue(
                        "error",
                        "GRAPH_NODE_DUPLICATE",
                        path,
                        f"node '{name}' is already defined",
                    )
                )
                continue

            fields, field_issues = resolver.fields_for(name, "semantic_graph")
            for field_issue in field_issues:
                issues.append(
                    GraphIssue(
                        "warning",
                        "GRAPH_NODE_FIELDS_UNKNOWN",
                        path,
                        field_issue.message,
                    )
                )
            field_names = fields or set()
            primary_keys = _primary_keys(obj)
            semantic = (
                obj.get("semantic") if isinstance(obj.get("semantic"), dict) else {}
            )
            declared_grain = semantic.get("grain") if semantic else None
            if isinstance(declared_grain, str):
                grain_fields = [declared_grain]
                grain_source = "semantic"
            elif isinstance(declared_grain, list) and all(
                isinstance(item, str) and item for item in declared_grain
            ):
                grain_fields = list(dict.fromkeys(declared_grain))
                grain_source = "semantic"
            else:
                grain_fields = list(primary_keys)
                grain_source = "primary_key" if primary_keys else "unknown"

            columns = []
            raw_columns = obj.get("columns") or []
            if isinstance(raw_columns, list):
                for column in raw_columns:
                    if not isinstance(column, dict) or not isinstance(
                        column.get("name"), str
                    ):
                        continue
                    properties = (
                        column.get("properties")
                        if isinstance(column.get("properties"), dict)
                        else {}
                    )
                    columns.append(
                        {
                            "name": column["name"],
                            "type": column.get("type"),
                            "description": properties.get("description"),
                        }
                    )
            if not columns and fields:
                columns = [{"name": field, "type": None} for field in sorted(fields)]

            properties = (
                obj.get("properties") if isinstance(obj.get("properties"), dict) else {}
            )
            relation = _relation_descriptor(kind, obj)
            node = {
                "name": name,
                "kind": kind,
                "label": obj.get("label"),
                "description": properties.get("description") or obj.get("description"),
                "primaryKey": list(primary_keys),
                "grain": {"fields": grain_fields, "source": grain_source},
                "attributes": columns,
                "entities": [],
                "metricBindings": [],
                "dimensionBindings": [],
                "relation": relation,
            }
            nodes.append(node)
            state[name] = {
                "artifact": node,
                "raw": obj,
                "field_names": set(field_names),
                "primary_keys": tuple(primary_keys),
                "kind": kind,
            }

    nodes.sort(key=lambda item: item["name"])
    return nodes, state


def attach_node_semantics(
    nodes: list[dict[str, Any]],
    *,
    node_entities: dict[str, list[dict[str, Any]]],
    metric_bindings: list[dict[str, Any]],
    dimension_bindings: list[dict[str, Any]],
) -> None:
    """Attach compiled entities and semantic member names to each graph node."""

    metrics_by_node: dict[str, list[str]] = defaultdict(list)
    dimensions_by_node: dict[str, list[str]] = defaultdict(list)
    for binding in metric_bindings:
        metrics_by_node[binding["model"]].append(binding["metric"])
    for binding in dimension_bindings:
        dimensions_by_node[binding["model"]].append(binding["dimension"])
    for node in nodes:
        name = node["name"]
        node["entities"] = sorted(
            node_entities.get(name, []),
            key=lambda item: (
                item["name"],
                item["type"],
                item.get("role") or "",
                item["fields"],
            ),
        )
        node["metricBindings"] = sorted(set(metrics_by_node.get(name, [])))
        node["dimensionBindings"] = sorted(set(dimensions_by_node.get(name, [])))


def _relation_descriptor(kind: str, obj: dict) -> dict[str, Any]:
    if kind == "view":
        return {"type": "sql", "sql": obj.get("statement")}
    if isinstance(obj.get("table_reference"), dict):
        return {
            "type": "table",
            "tableReference": deepcopy(obj["table_reference"]),
        }
    return {"type": "sql", "sql": obj.get("ref_sql")}


def _primary_keys(obj: dict) -> tuple[str, ...]:
    raw = obj.get("primary_key")
    if isinstance(raw, str) and raw:
        return (raw,)
    if isinstance(raw, list):
        values = tuple(item for item in raw if isinstance(item, str) and item)
        if values:
            return values
    inferred: list[str] = []
    for column in obj.get("columns") or []:
        if not isinstance(column, dict) or not isinstance(column.get("name"), str):
            continue
        properties = (
            column.get("properties")
            if isinstance(column.get("properties"), dict)
            else {}
        )
        if properties.get("is_row_unique_id") is True:
            inferred.append(column["name"])
    return tuple(inferred)

"""Project ontology relationships onto Apache Ossie relationship objects."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from wren.semantic_graph.ontology_osi_common import (
    foreign_custom_extensions,
    make_ai_context,
    wren_extension,
)
from wren.semantic_graph.ontology_utils import diagnostic, string_list


def export_osi_relationships(
    nodes: Mapping[str, Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    diagnostics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for edge in sorted(
        (edge for edge in edges if edge.get("type") == "RELATIONSHIP"),
        key=lambda item: str(item.get("id", "")),
    ):
        properties = edge.get("properties")
        properties = properties if isinstance(properties, Mapping) else {}
        source_node = nodes.get(str(edge.get("sourceId")))
        target_node = nodes.get(str(edge.get("targetId")))
        if source_node is None or target_node is None:
            diagnostics.append(
                diagnostic(
                    "warning",
                    "OSSIE_EXPORT_RELATIONSHIP_EXTENSION_ONLY",
                    str(edge.get("id", "relationship")),
                    "relationship endpoints are missing and were preserved only in the WREN extension",
                )
            )
            continue
        cardinality = str(properties.get("cardinality") or "MANY_TO_ONE").upper()
        source_name = str(source_node.get("name"))
        target_name = str(target_node.get("name"))
        from_columns, to_columns = _relationship_columns(
            properties, source_name, target_name
        )
        if cardinality == "ONE_TO_MANY":
            source_name, target_name = target_name, source_name
            from_columns, to_columns = to_columns, from_columns
        elif cardinality == "MANY_TO_MANY":
            diagnostics.append(
                diagnostic(
                    "warning",
                    "OSSIE_EXPORT_MANY_TO_MANY_EXTENSION_ONLY",
                    str(edge.get("id", "relationship")),
                    "Apache Ossie core only models many-to-one relationships",
                )
            )
            continue
        elif cardinality == "ONE_TO_ONE":
            diagnostics.append(
                diagnostic(
                    "warning",
                    "OSSIE_EXPORT_ONE_TO_ONE_APPROXIMATED",
                    str(edge.get("id", "relationship")),
                    "one-to-one was emitted as many-to-one and the original cardinality remains in the WREN extension",
                )
            )
        if not from_columns or len(from_columns) != len(to_columns):
            diagnostics.append(
                diagnostic(
                    "warning",
                    "OSSIE_EXPORT_RELATIONSHIP_KEYS_EXTENSION_ONLY",
                    str(edge.get("id", "relationship")),
                    "relationship has no aligned join columns and was preserved only in the WREN extension",
                )
            )
            continue
        relationship: dict[str, Any] = {
            "name": edge.get("name") or edge.get("id"),
            "from": source_name,
            "to": target_name,
            "from_columns": from_columns,
            "to_columns": to_columns,
        }
        description = properties.get("description")
        synonyms = properties.get("synonyms")
        ai_context = make_ai_context(
            synonyms=synonyms,
            instructions=properties.get("aiInstructions") or description,
            examples=properties.get("aiExamples"),
        )
        if ai_context:
            relationship["ai_context"] = ai_context
        relationship["custom_extensions"] = foreign_custom_extensions(edge) + [
            wren_extension(
                {
                    "ontology_edge": deepcopy(dict(edge)),
                    "original_cardinality": cardinality,
                }
            )
        ]
        result.append(relationship)
    return result


def _relationship_columns(
    properties: Mapping[str, Any], source_name: str, target_name: str
) -> tuple[list[str], list[str]]:
    from_columns = properties.get("fromColumns")
    to_columns = properties.get("toColumns")
    if isinstance(from_columns, list) and isinstance(to_columns, list):
        return string_list(from_columns), string_list(to_columns)
    condition_columns = properties.get("conditionColumns")
    if isinstance(condition_columns, Mapping):
        return (
            string_list(condition_columns.get(source_name)),
            string_list(condition_columns.get(target_name)),
        )
    return [], []

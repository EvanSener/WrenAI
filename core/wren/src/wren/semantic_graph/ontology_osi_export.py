"""Export ontology graphs through the Apache Ossie core plus WREN extension."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from wren.semantic_graph.ontology_osi_common import (
    apply_osi_semantics,
    foreign_custom_extensions,
    node_non_core_extension,
    relation_source,
    wren_extension,
)
from wren.semantic_graph.ontology_osi_relationships import export_osi_relationships
from wren.semantic_graph.ontology_types import (
    OSSIE_VERSION,
    OntologyInterchangeError,
    validate_ontology_graph,
)
from wren.semantic_graph.ontology_utils import (
    diagnostic,
    is_time_type,
    to_osi_expression,
)


def export_ontology_to_osi(
    ontology_graph: Mapping[str, Any],
    *,
    semantic_model_name: str | None = None,
    dialect: str = "ANSI_SQL",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Export the Ossie-expressible projection plus a lossless WREN extension.

    The returned diagnostics enumerate every category that required an
    extension or could not be emitted as an Ossie core object.  A complete
    ontology snapshot is embedded in the semantic model's WREN extension.
    """

    validate_ontology_graph(ontology_graph)
    nodes = {
        item["id"]: item
        for item in ontology_graph.get("nodes", [])
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    edges = [
        item for item in ontology_graph.get("edges", []) if isinstance(item, Mapping)
    ]
    diagnostics: list[dict[str, Any]] = []

    roots = sorted(
        (node for node in nodes.values() if node.get("type") == "SEMANTIC_MODEL"),
        key=lambda item: item["id"],
    )
    root = roots[0] if roots else None
    model_name = (
        semantic_model_name
        or (root.get("name") if root else None)
        or "wren_semantic_model"
    )

    datasets: list[dict[str, Any]] = []
    dataset_nodes = sorted(
        (node for node in nodes.values() if node.get("type") == "DATASET"),
        key=lambda item: item["id"],
    )
    if not dataset_nodes:
        raise OntologyInterchangeError(
            [
                diagnostic(
                    "error",
                    "OSSIE_EXPORT_DATASET_REQUIRED",
                    "nodes",
                    "Apache Ossie requires at least one dataset",
                )
            ]
        )
    for dataset_node in dataset_nodes:
        datasets.append(
            _export_osi_dataset(dataset_node, nodes, edges, dialect, diagnostics)
        )

    metrics = []
    for metric_node in sorted(
        (node for node in nodes.values() if node.get("type") == "METRIC"),
        key=lambda item: item["id"],
    ):
        expression = metric_node.get("properties", {}).get("expression")
        if not isinstance(expression, (str, Mapping)) or not expression:
            diagnostics.append(
                diagnostic(
                    "warning",
                    "OSSIE_EXPORT_METRIC_EXTENSION_ONLY",
                    metric_node["id"],
                    "metric has no core-compatible expression and is preserved in the WREN extension",
                )
            )
            continue
        metric = {
            "name": metric_node["name"],
            "expression": to_osi_expression(expression, dialect),
        }
        apply_osi_semantics(metric, metric_node, include_label=False)
        foreign = foreign_custom_extensions(metric_node)
        local_extension = node_non_core_extension(metric_node, exclude={"expression"})
        metric["custom_extensions"] = foreign + [wren_extension(local_extension)]
        metrics.append(metric)

    relationships = export_osi_relationships(nodes, edges, diagnostics)
    semantic_model: dict[str, Any] = {
        "name": str(model_name),
        "datasets": datasets,
    }
    if relationships:
        semantic_model["relationships"] = relationships
    if metrics:
        semantic_model["metrics"] = metrics
    if root:
        apply_osi_semantics(semantic_model, root, include_label=False)

    unsupported_types = sorted(
        {
            node.get("type")
            for node in nodes.values()
            if node.get("type") in {"DIMENSION", "CUBE", "HIERARCHY"}
        }
    )
    if unsupported_types:
        diagnostics.append(
            diagnostic(
                "warning",
                "OSSIE_EXPORT_ONTOLOGY_EXTENSION",
                "nodes",
                "Apache Ossie has no core objects for "
                + ", ".join(unsupported_types)
                + "; they are preserved in the WREN extension",
            )
        )
    unsupported_edges = sorted(
        {
            edge.get("type")
            for edge in edges
            if edge.get("type")
            not in {"HAS_DATASET", "HAS_FIELD", "HAS_METRIC", "RELATIONSHIP"}
        }
    )
    if unsupported_edges:
        diagnostics.append(
            diagnostic(
                "info",
                "OSSIE_EXPORT_EDGE_EXTENSION",
                "edges",
                "non-core graph edges are preserved in the WREN extension: "
                + ", ".join(unsupported_edges),
            )
        )

    snapshot = deepcopy(dict(ontology_graph))
    extension_payload = {
        "ontology_schema_version": ontology_graph.get("schemaVersion"),
        "ontology_graph": snapshot,
        "export_diagnostics": diagnostics,
    }
    semantic_model["custom_extensions"] = foreign_custom_extensions(root) + [
        wren_extension(extension_payload)
    ]
    return {"version": OSSIE_VERSION, "semantic_model": [semantic_model]}, diagnostics


def save_osi_document(document: Mapping[str, Any], output: Path) -> Path:
    """Write an exported Ossie document as JSON or YAML based on the suffix."""

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() == ".json":
        text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    else:
        text = yaml.safe_dump(
            dict(document), sort_keys=False, allow_unicode=True, width=1000
        )
    output.write_text(text, encoding="utf-8")
    return output


def export_ontology_to_osi_file(
    ontology_graph: Mapping[str, Any],
    output: Path,
    *,
    semantic_model_name: str | None = None,
    dialect: str = "ANSI_SQL",
) -> tuple[Path, list[dict[str, Any]]]:
    """Export and save an Ossie YAML/JSON document in one call."""

    document, diagnostics = export_ontology_to_osi(
        ontology_graph,
        semantic_model_name=semantic_model_name,
        dialect=dialect,
    )
    return save_osi_document(document, output), diagnostics


def _export_osi_dataset(
    dataset_node: Mapping[str, Any],
    nodes: Mapping[str, Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    dialect: str,
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    dataset_id = dataset_node["id"]
    properties = dataset_node.get("properties")
    properties = properties if isinstance(properties, Mapping) else {}
    relation = properties.get("relation")
    source = properties.get("source")
    if not isinstance(source, str) or not source:
        source = relation_source(relation)
    if not source:
        source = str(dataset_node.get("name") or "unknown_dataset")
        diagnostics.append(
            diagnostic(
                "warning",
                "OSSIE_EXPORT_DATASET_SOURCE_INFERRED",
                dataset_id,
                f"dataset source was missing; used '{source}' and preserved original metadata in WREN extension",
            )
        )
    dataset: dict[str, Any] = {"name": dataset_node["name"], "source": source}
    primary_key = properties.get("primaryKey")
    if isinstance(primary_key, str) and primary_key:
        dataset["primary_key"] = [primary_key]
    elif isinstance(primary_key, list) and primary_key:
        dataset["primary_key"] = [str(value) for value in primary_key if value]
    unique_keys = properties.get("uniqueKeys")
    if isinstance(unique_keys, list) and unique_keys:
        dataset["unique_keys"] = deepcopy(unique_keys)
    apply_osi_semantics(dataset, dataset_node, include_label=False)

    field_edges = sorted(
        (
            edge
            for edge in edges
            if edge.get("type") == "HAS_FIELD" and edge.get("sourceId") == dataset_id
        ),
        key=lambda edge: (
            edge.get("properties", {}).get("ordinal", 0),
            str(edge.get("targetId", "")),
        ),
    )
    fields: dict[str, dict[str, Any]] = {}
    column_types: dict[str, str] = {}
    for edge in field_edges:
        field_node = nodes.get(str(edge.get("targetId")))
        if field_node is None:
            continue
        field = _export_osi_field(field_node, dialect)
        fields[field["name"]] = field
        field_type = field_node.get("properties", {}).get("type")
        if isinstance(field_type, str) and field_type:
            column_types[field["name"]] = field_type

    dimension_edges = sorted(
        (
            edge
            for edge in edges
            if edge.get("type") == "DIMENSION_BINDING"
            and edge.get("targetId") == dataset_id
        ),
        key=lambda edge: str(edge.get("sourceId", "")),
    )
    for edge in dimension_edges:
        dimension_node = nodes.get(str(edge.get("sourceId")))
        if dimension_node is None:
            continue
        field = _export_osi_field(dimension_node, dialect)
        existing = fields.get(field["name"])
        if existing is None:
            fields[field["name"]] = field
        else:
            existing.update(
                {
                    key: value
                    for key, value in field.items()
                    if key not in {"name", "custom_extensions"}
                }
            )
        field_type = dimension_node.get("properties", {}).get("type")
        if isinstance(field_type, str) and field_type:
            column_types[field["name"]] = field_type

    dataset["fields"] = list(fields.values())
    non_core = node_non_core_extension(
        dataset_node,
        exclude={"source", "primaryKey", "uniqueKeys", "relation"},
    )
    if column_types:
        non_core["column_types"] = column_types
        diagnostics.append(
            diagnostic(
                "info",
                "OSSIE_EXPORT_COLUMN_TYPES_EXTENSION",
                dataset_id,
                "field types are preserved in the dataset WREN extension",
            )
        )
    dataset["custom_extensions"] = foreign_custom_extensions(dataset_node) + [
        wren_extension(non_core)
    ]
    return dataset


def _export_osi_field(node: Mapping[str, Any], dialect: str) -> dict[str, Any]:
    properties = node.get("properties")
    properties = properties if isinstance(properties, Mapping) else {}
    expression = properties.get("expression") or node.get("name")
    field: dict[str, Any] = {
        "name": node["name"],
        "expression": to_osi_expression(expression, dialect),
    }
    is_time = properties.get("isTime") is True or is_time_type(properties.get("type"))
    if node.get("type") == "DIMENSION" or is_time:
        field["dimension"] = {"is_time": is_time}
    apply_osi_semantics(field, node, include_label=True)
    non_core = node_non_core_extension(
        node, exclude={"expression", "isTime", "dataset", "ordinal"}
    )
    if non_core:
        field["custom_extensions"] = foreign_custom_extensions(node) + [
            wren_extension(non_core)
        ]
    elif foreign := foreign_custom_extensions(node):
        field["custom_extensions"] = foreign
    return field

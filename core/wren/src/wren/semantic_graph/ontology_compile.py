"""Compile Wren semantic graph artifacts into the ontology sidecar."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from wren.context import load_cubes
from wren.semantic_graph.ontology_cubes import compile_wren_cubes
from wren.semantic_graph.ontology_types import GraphBuilder, validate_ontology_graph
from wren.semantic_graph.ontology_utils import edge_id, node_id, nonempty


def compile_ontology_graph(
    semantic_graph: Mapping[str, Any],
    project_path: Path | None = None,
    *,
    cubes: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compile semantic metadata and native Cube hierarchies into a sidecar graph.

    ``semantic_graph`` is the output of :func:`compile_graph_bundle`.  Cubes can
    be supplied directly (useful to embedders), otherwise they are loaded from
    ``project_path`` using Wren's existing schema-version-aware loader.
    """

    if not isinstance(semantic_graph, Mapping):
        raise TypeError("semantic_graph must be a mapping")
    if cubes is None:
        cubes = load_cubes(Path(project_path)) if project_path is not None else []

    builder = GraphBuilder()
    project = semantic_graph.get("project")
    project = project if isinstance(project, Mapping) else {}
    project_name = nonempty(project.get("name")) or "wren_project"
    root_id = node_id("SEMANTIC_MODEL", project_name)
    builder.add_node(
        node_id=root_id,
        node_type="SEMANTIC_MODEL",
        name=project_name,
        label=project.get("label"),
        description=project.get("description"),
        synonyms=project.get("synonyms"),
        properties={
            "projectVersion": project.get("version"),
            "dataSource": project.get("dataSource"),
            "semanticGraphSchemaVersion": semantic_graph.get("schemaVersion"),
        },
    )

    _compile_wren_datasets(builder, root_id, semantic_graph.get("nodes"))
    _compile_wren_members(
        builder,
        root_id,
        semantic_graph.get("metrics"),
        member_type="METRIC",
    )
    _compile_wren_members(
        builder,
        root_id,
        semantic_graph.get("dimensions"),
        member_type="DIMENSION",
    )
    _compile_wren_relationships(builder, semantic_graph.get("edges"))
    _compile_wren_bindings(
        builder,
        semantic_graph.get("metricBindings"),
        member_type="METRIC",
    )
    _compile_wren_bindings(
        builder,
        semantic_graph.get("dimensionBindings"),
        member_type="DIMENSION",
    )
    compile_wren_cubes(builder, root_id, cubes)

    return builder.artifact(
        source={
            "format": "wren-semantic-graph",
            "project": project_name,
            "schemaVersion": semantic_graph.get("schemaVersion"),
        },
        read_only=False,
    )


def save_ontology_graph(
    ontology_graph: Mapping[str, Any],
    project_path: Path,
    *,
    output: Path | None = None,
) -> Path:
    """Save an ontology graph, defaulting to ``target/ontology_graph.json``."""

    validate_ontology_graph(ontology_graph)
    path = (
        Path(output)
        if output is not None
        else Path(project_path) / "target" / "ontology_graph.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(ontology_graph, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def load_ontology_graph(path: Path) -> dict[str, Any]:
    """Load a saved graph; a project directory resolves to its target sidecar."""

    path = Path(path)
    if path.is_dir():
        path = path / "target" / "ontology_graph.json"
    graph = json.loads(path.read_text(encoding="utf-8"))
    validate_ontology_graph(graph)
    return graph


def _compile_wren_datasets(builder: GraphBuilder, root_id: str, raw_nodes: Any) -> None:
    for index, item in enumerate(raw_nodes if isinstance(raw_nodes, list) else []):
        if not isinstance(item, Mapping) or not nonempty(item.get("name")):
            builder.issue(
                "warning",
                "ONTOLOGY_DATASET_INVALID",
                f"semantic_graph.nodes[{index}]",
                "node without a stable name was skipped",
            )
            continue
        name = str(item["name"])
        dataset_id = node_id("DATASET", name)
        builder.add_node(
            node_id=dataset_id,
            node_type="DATASET",
            name=name,
            label=item.get("label"),
            description=item.get("description"),
            synonyms=item.get("synonyms"),
            properties={
                "kind": item.get("kind"),
                "primaryKey": item.get("primaryKey"),
                "grain": item.get("grain"),
                "entities": item.get("entities"),
                "relation": item.get("relation"),
            },
        )
        builder.add_edge(
            edge_id=edge_id("HAS_DATASET", root_id, dataset_id),
            edge_type="HAS_DATASET",
            source_id=root_id,
            target_id=dataset_id,
        )
        attributes = item.get("attributes")
        for field_index, field in enumerate(
            attributes if isinstance(attributes, list) else []
        ):
            if not isinstance(field, Mapping) or not nonempty(field.get("name")):
                continue
            field_name = str(field["name"])
            field_id = node_id("FIELD", name, field_name)
            builder.add_node(
                node_id=field_id,
                node_type="FIELD",
                name=field_name,
                label=field.get("label"),
                description=field.get("description"),
                synonyms=field.get("synonyms"),
                properties={
                    "dataset": name,
                    "type": field.get("type"),
                    "expression": field.get("expression") or field_name,
                    "ordinal": field_index,
                },
            )
            builder.add_edge(
                edge_id=edge_id("HAS_FIELD", dataset_id, field_id),
                edge_type="HAS_FIELD",
                source_id=dataset_id,
                target_id=field_id,
                properties={"ordinal": field_index},
            )


def _compile_wren_members(
    builder: GraphBuilder,
    root_id: str,
    raw_members: Any,
    *,
    member_type: str,
) -> None:
    member_edge_type = "HAS_METRIC" if member_type == "METRIC" else "HAS_DIMENSION"
    for index, item in enumerate(raw_members if isinstance(raw_members, list) else []):
        if not isinstance(item, Mapping) or not nonempty(item.get("name")):
            builder.issue(
                "warning",
                f"ONTOLOGY_{member_type}_INVALID",
                f"semantic_graph.{member_type.lower()}s[{index}]",
                "semantic member without a stable name was skipped",
            )
            continue
        name = str(item["name"])
        member_id = node_id(member_type, name)
        builder.add_node(
            node_id=member_id,
            node_type=member_type,
            name=name,
            label=item.get("label"),
            description=item.get("description"),
            synonyms=item.get("synonyms"),
            properties={
                key: deepcopy(value)
                for key, value in item.items()
                if key not in {"name", "label", "description", "synonyms"}
            },
        )
        builder.add_edge(
            edge_id=edge_id(member_edge_type, root_id, member_id),
            edge_type=member_edge_type,
            source_id=root_id,
            target_id=member_id,
        )


def _compile_wren_relationships(builder: GraphBuilder, raw_edges: Any) -> None:
    for index, item in enumerate(raw_edges if isinstance(raw_edges, list) else []):
        if not isinstance(item, Mapping) or not nonempty(item.get("name")):
            continue
        models = item.get("declaredModels")
        if not (
            isinstance(models, list)
            and len(models) == 2
            and all(isinstance(value, str) and value for value in models)
        ):
            builder.issue(
                "warning",
                "ONTOLOGY_RELATIONSHIP_ENDPOINTS_INVALID",
                f"semantic_graph.edges[{index}]",
                "relationship was preserved only in diagnostics because it has no two declared models",
            )
            continue
        source_id = builder.ensure_node(
            "DATASET", models[0], path=f"semantic_graph.edges[{index}]"
        )
        target_id = builder.ensure_node(
            "DATASET", models[1], path=f"semantic_graph.edges[{index}]"
        )
        name = str(item["name"])
        builder.add_edge(
            edge_id=edge_id("RELATIONSHIP", name),
            edge_type="RELATIONSHIP",
            source_id=source_id,
            target_id=target_id,
            name=name,
            properties={
                key: deepcopy(value)
                for key, value in item.items()
                if key not in {"name", "declaredModels"}
            }
            | {"declaredModels": list(models)},
        )


def _compile_wren_bindings(
    builder: GraphBuilder, raw_bindings: Any, *, member_type: str
) -> None:
    member_key = "metric" if member_type == "METRIC" else "dimension"
    binding_type = "METRIC_BINDING" if member_type == "METRIC" else "DIMENSION_BINDING"
    for index, item in enumerate(
        raw_bindings if isinstance(raw_bindings, list) else []
    ):
        if not isinstance(item, Mapping):
            continue
        member = nonempty(item.get(member_key))
        model = nonempty(item.get("model"))
        if member is None or model is None:
            continue
        member_id = builder.ensure_node(
            member_type, member, path=f"semantic_graph.{member_key}Bindings[{index}]"
        )
        dataset_id = builder.ensure_node(
            "DATASET", model, path=f"semantic_graph.{member_key}Bindings[{index}]"
        )
        binding_id = nonempty(item.get("id")) or f"{member}@{model}"
        builder.add_edge(
            edge_id=edge_id(binding_type, binding_id),
            edge_type=binding_type,
            source_id=member_id,
            target_id=dataset_id,
            name=binding_id,
            properties={
                key: deepcopy(value)
                for key, value in item.items()
                if key not in {"id", member_key, "model"}
            },
        )

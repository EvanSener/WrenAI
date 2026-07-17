"""Orchestrate compilation of additive semantic graph artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wren.context import load_models, load_project_config, load_views
from wren.dimension_compiler import load_dimensions
from wren.metric_compiler import dialect_for, load_metrics
from wren.semantic_graph.config import (
    GRAPH_SCHEMA_VERSION,
    RELATIONSHIP_FILE,
    load_relationship_document,
    parse_graph_config,
)
from wren.semantic_graph.edges import compile_edges
from wren.semantic_graph.members import compile_dimensions, compile_metrics
from wren.semantic_graph.model import (
    GraphBundle,
    GraphCompilationError,
    GraphIssue,
)
from wren.semantic_graph.nodes import attach_node_semantics, compile_nodes


def compile_graph_bundle(
    project_path: Path, *, max_hops: int | None = None
) -> GraphBundle:
    """Compile graph and queryability artifacts without touching old MDL state."""

    project_path = Path(project_path)
    issues: list[GraphIssue] = []
    project_config = load_project_config(project_path)
    models = load_models(project_path)
    views = load_views(project_path)
    metrics = load_metrics(project_path)
    dimensions = load_dimensions(project_path)
    dialect = dialect_for(project_config.get("data_source"))

    relationship_document = load_relationship_document(project_path, issues)
    graph_config = parse_graph_config(
        relationship_document.get("graph"), issues, max_hops=max_hops
    )

    nodes, node_state = compile_nodes(models, views, dialect, issues)
    edges, node_entities = compile_edges(
        relationship_document.get("relationships"),
        node_state,
        graph_config,
        dialect,
        issues,
    )
    metric_defs, metric_bindings, metric_conflicts = compile_metrics(
        metrics, node_state, graph_config, dialect, issues
    )
    dimension_defs, dimension_bindings, conflicts = compile_dimensions(
        dimensions, node_state, graph_config, dialect, issues
    )
    attach_node_semantics(
        nodes,
        node_entities=node_entities,
        metric_bindings=metric_bindings,
        dimension_bindings=dimension_bindings,
    )

    errors = [issue for issue in issues if issue.level == "error"]
    if errors:
        raise GraphCompilationError(errors)

    semantic_graph: dict[str, Any] = {
        "schemaVersion": GRAPH_SCHEMA_VERSION,
        "project": {
            "name": project_config.get("name"),
            "version": project_config.get("version"),
            "dataSource": project_config.get("data_source"),
        },
        "edgeSource": RELATIONSHIP_FILE,
        "config": graph_config.as_dict(),
        "nodes": nodes,
        "edges": edges,
        "metrics": metric_defs,
        "dimensions": dimension_defs,
        "metricBindings": metric_bindings,
        "dimensionBindings": dimension_bindings,
        "attributeConflicts": conflicts,
        "bindingConflicts": [
            *metric_conflicts,
            *(
                {
                    "kind": "dimension",
                    "member": item["attribute"],
                    "candidateModels": item["candidateModels"],
                    "masterModel": item["masterModel"],
                    "resolution": item["resolution"],
                }
                for item in conflicts
            ),
        ],
        "diagnostics": [issue.as_dict() for issue in issues],
    }

    from wren.semantic_graph.queryability import (  # noqa: PLC0415
        build_queryability_index,
    )

    queryability_index = build_queryability_index(
        semantic_graph, max_hops=graph_config.max_hops
    )
    return GraphBundle(
        semantic_graph=semantic_graph,
        queryability_index=queryability_index,
        issues=tuple(issues),
    )


def save_graph_bundle(
    bundle: GraphBundle,
    project_path: Path,
    *,
    graph_output: Path | None = None,
    index_output: Path | None = None,
) -> tuple[Path, Path]:
    """Persist deterministic graph artifacts under the project's target folder."""

    project_path = Path(project_path)
    graph_path = graph_output or project_path / "target" / "semantic_graph.json"
    index_path = index_output or project_path / "target" / "queryability_index.json"
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(
        json.dumps(bundle.semantic_graph, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    index_path.write_text(
        json.dumps(bundle.queryability_index, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return graph_path, index_path

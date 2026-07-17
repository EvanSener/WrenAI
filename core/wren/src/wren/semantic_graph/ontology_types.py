"""Ontology graph constants, diagnostics, builder, and shape validation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from wren.semantic_graph.ontology_utils import (
    clean_mapping,
    diagnostic,
    node_id,
    string_list,
)

ONTOLOGY_SCHEMA_VERSION = 1
ONTOLOGY_KIND = "WREN_ONTOLOGY_GRAPH"
OSSIE_VERSION = "0.2.0.dev0"
WREN_VENDOR_NAME = "WREN"

NODE_TYPES = frozenset(
    {
        "SEMANTIC_MODEL",
        "DATASET",
        "FIELD",
        "METRIC",
        "DIMENSION",
        "CUBE",
        "HIERARCHY",
    }
)

EDGE_TYPES = frozenset(
    {
        "HAS_DATASET",
        "HAS_FIELD",
        "HAS_METRIC",
        "HAS_DIMENSION",
        "HAS_CUBE",
        "RELATIONSHIP",
        "METRIC_BINDING",
        "DIMENSION_BINDING",
        "CUBE_BASE_DATASET",
        "CUBE_METRIC",
        "CUBE_DIMENSION",
        "CUBE_TIME_DIMENSION",
        "CUBE_HIERARCHY",
        "HIERARCHY_LEVEL",
    }
)


class OntologyInterchangeError(ValueError):
    """Raised when an ontology or Ossie document cannot be represented safely."""

    def __init__(self, diagnostics: Sequence[Mapping[str, Any]]):
        self.diagnostics = tuple(dict(item) for item in diagnostics)
        details = "\n".join(
            f"- [{item.get('level', 'error').upper()}] "
            f"{item.get('code', 'ONTOLOGY_ERROR')}: {item.get('message', '')}"
            for item in self.diagnostics
        )
        super().__init__(f"Ontology interchange failed:\n{details}")


class GraphBuilder:
    """Deterministically assemble typed ontology nodes, edges, and diagnostics."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[str, dict[str, Any]] = {}
        self.diagnostics: list[dict[str, Any]] = []

    def add_node(
        self,
        *,
        node_id: str,
        node_type: str,
        name: str,
        label: Any = None,
        description: Any = None,
        synonyms: Any = None,
        properties: Mapping[str, Any] | None = None,
        extensions: Mapping[str, Any] | None = None,
    ) -> str:
        if node_type not in NODE_TYPES:
            raise ValueError(f"unknown ontology node type: {node_type}")
        node = {
            "id": node_id,
            "type": node_type,
            "name": name,
            "label": label if isinstance(label, str) and label else name,
            "description": description
            if isinstance(description, str) and description
            else None,
            "synonyms": string_list(synonyms),
            "properties": clean_mapping(properties),
        }
        if extensions:
            node["extensions"] = deepcopy(dict(extensions))
        current = self.nodes.get(node_id)
        if current is None or current.get("properties", {}).get("unresolved"):
            self.nodes[node_id] = node
        return node_id

    def ensure_node(self, node_type: str, name: str, *, path: str) -> str:
        identifier = node_id(node_type, name)
        if identifier not in self.nodes:
            self.add_node(
                node_id=identifier,
                node_type=node_type,
                name=name,
                properties={"unresolved": True},
            )
            self.issue(
                "warning",
                "ONTOLOGY_REFERENCE_UNRESOLVED",
                path,
                f"created a placeholder {node_type.lower()} for '{name}'",
            )
        return identifier

    def add_edge(
        self,
        *,
        edge_id: str,
        edge_type: str,
        source_id: str,
        target_id: str,
        name: str | None = None,
        properties: Mapping[str, Any] | None = None,
        extensions: Mapping[str, Any] | None = None,
    ) -> str:
        if edge_type not in EDGE_TYPES:
            raise ValueError(f"unknown ontology edge type: {edge_type}")
        edge = {
            "id": edge_id,
            "type": edge_type,
            "sourceId": source_id,
            "targetId": target_id,
            "properties": clean_mapping(properties),
        }
        if name:
            edge["name"] = name
        if extensions:
            edge["extensions"] = deepcopy(dict(extensions))
        self.edges[edge_id] = edge
        return edge_id

    def issue(self, level: str, code: str, path: str, message: str) -> None:
        self.diagnostics.append(
            {"level": level, "code": code, "path": path, "message": message}
        )

    def artifact(
        self,
        *,
        source: Mapping[str, Any],
        read_only: bool,
        extensions: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        artifact: dict[str, Any] = {
            "schemaVersion": ONTOLOGY_SCHEMA_VERSION,
            "kind": ONTOLOGY_KIND,
            "readOnly": read_only,
            "source": deepcopy(dict(source)),
            "nodeTypes": sorted(NODE_TYPES),
            "edgeTypes": sorted(EDGE_TYPES),
            "nodes": sorted(self.nodes.values(), key=lambda item: item["id"]),
            "edges": sorted(self.edges.values(), key=lambda item: item["id"]),
            "diagnostics": sorted(
                self.diagnostics,
                key=lambda item: (
                    item.get("level", ""),
                    item.get("code", ""),
                    item.get("path", ""),
                    item.get("message", ""),
                ),
            ),
        }
        if extensions:
            artifact["extensions"] = deepcopy(dict(extensions))
        return artifact


def validate_ontology_graph(graph: Mapping[str, Any]) -> None:
    diagnostics: list[dict[str, Any]] = []
    if not isinstance(graph, Mapping) or graph.get("kind") != ONTOLOGY_KIND:
        diagnostics.append(
            diagnostic(
                "error",
                "ONTOLOGY_KIND_INVALID",
                "kind",
                f"expected {ONTOLOGY_KIND!r}",
            )
        )
    nodes = graph.get("nodes") if isinstance(graph, Mapping) else None
    edges = graph.get("edges") if isinstance(graph, Mapping) else None
    if not isinstance(nodes, list) or not isinstance(edges, list):
        diagnostics.append(
            diagnostic(
                "error",
                "ONTOLOGY_GRAPH_SHAPE_INVALID",
                "<graph>",
                "nodes and edges must be lists",
            )
        )
    else:
        node_ids = {
            node.get("id")
            for node in nodes
            if isinstance(node, Mapping) and isinstance(node.get("id"), str)
        }
        for edge in edges:
            if not isinstance(edge, Mapping):
                continue
            if (
                edge.get("sourceId") not in node_ids
                or edge.get("targetId") not in node_ids
            ):
                diagnostics.append(
                    diagnostic(
                        "error",
                        "ONTOLOGY_EDGE_ENDPOINT_MISSING",
                        str(edge.get("id", "<edge>")),
                        "edge sourceId and targetId must reference graph nodes",
                    )
                )
    if diagnostics:
        raise OntologyInterchangeError(diagnostics)

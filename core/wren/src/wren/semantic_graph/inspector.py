"""Deterministic runtime for safe, read-only graph inspection queries."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wren.semantic_graph.inspection_syntax import (
    GraphInspectionError,
    _MatchPattern,
    _NodePattern,
    _ParsedQuery,
    _Predicate,
    _PropertyRef,
    _ReturnItem,
    _VariableRef,
    parse_inspection_query,
)

_MISSING = object()


@dataclass(frozen=True)
class _Node:
    identifier: str
    record: dict[str, Any]
    labels: frozenset[str]


@dataclass(frozen=True)
class _Edge:
    identifier: str
    source: str
    target: str
    record: dict[str, Any]
    labels: frozenset[str]


@dataclass(frozen=True)
class _BoundValue:
    record: dict[str, Any]
    virtual: dict[str, Any]


@dataclass(frozen=True)
class _Graph:
    kind: str
    nodes: tuple[_Node, ...]
    edges: tuple[_Edge, ...]
    nodes_by_id: dict[str, _Node]


def inspect_graph(
    graph: Mapping[str, Any] | str | Path,
    query: str,
    parameters: Mapping[str, Any] | None = None,
    *,
    max_rows: int = 10_000,
) -> dict[str, Any]:
    """Run a validated inspection query and return JSON rows plus explain."""

    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 1:
        raise GraphInspectionError(
            "INVALID_MAX_ROWS", "max_rows must be a positive integer"
        )

    parsed = parse_inspection_query(
        query,
        {} if parameters is None else parameters,
        max_rows=max_rows,
    )
    normalized = _load_and_normalize_graph(graph)
    return _execute(normalized, parsed, max_rows=max_rows)


def _load_and_normalize_graph(graph: Mapping[str, Any] | str | Path) -> _Graph:
    if isinstance(graph, (str, Path)):
        path = Path(graph)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise GraphInspectionError(
                "GRAPH_READ_ERROR", f"cannot read graph artifact '{path}': {exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise GraphInspectionError(
                "INVALID_GRAPH_JSON",
                f"graph artifact '{path}' is not valid JSON: {exc.msg}",
                position=exc.pos,
            ) from exc
    elif isinstance(graph, Mapping):
        document = graph
    else:
        raise GraphInspectionError(
            "INVALID_GRAPH", "graph must be a mapping or JSON file path"
        )

    if not isinstance(document, Mapping):
        raise GraphInspectionError("INVALID_GRAPH", "graph JSON root must be an object")
    raw_nodes = document.get("nodes")
    raw_edges = document.get("edges")
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise GraphInspectionError(
            "INVALID_GRAPH", "graph must contain 'nodes' and 'edges' arrays"
        )

    kind = _graph_kind(document)
    nodes: list[_Node] = []
    by_id: dict[str, _Node] = {}
    for index, raw in enumerate(raw_nodes):
        record = _record(raw, f"nodes[{index}]")
        identifier = _identifier(record, ("id", "name"), f"nodes[{index}]")
        if identifier in by_id:
            raise GraphInspectionError(
                "INVALID_GRAPH", f"duplicate node identifier '{identifier}'"
            )
        labels = _labels(record, is_edge=False)
        node = _Node(identifier, record, labels)
        nodes.append(node)
        by_id[identifier] = node

    edges: list[_Edge] = []
    edge_ids: set[str] = set()
    for index, raw in enumerate(raw_edges):
        record = _record(raw, f"edges[{index}]")
        identifier = _identifier(record, ("id", "name"), f"edges[{index}]")
        if identifier in edge_ids:
            raise GraphInspectionError(
                "INVALID_GRAPH", f"duplicate edge identifier '{identifier}'"
            )
        edge_ids.add(identifier)
        source, target = _edge_endpoints(record, f"edges[{index}]")
        missing = [value for value in (source, target) if value not in by_id]
        if missing:
            raise GraphInspectionError(
                "INVALID_GRAPH",
                f"edge '{identifier}' references unknown node(s): "
                + ", ".join(missing),
            )
        edges.append(
            _Edge(identifier, source, target, record, _labels(record, is_edge=True))
        )

    nodes.sort(key=lambda item: (item.identifier, _json_text(item.record)))
    edges.sort(key=lambda item: (item.identifier, item.source, item.target))
    return _Graph(kind, tuple(nodes), tuple(edges), by_id)


def _record(raw: Any, path: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise GraphInspectionError("INVALID_GRAPH", f"{path} must be an object")
    record = dict(raw)
    try:
        json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise GraphInspectionError(
            "INVALID_GRAPH", f"{path} must contain only JSON-compatible values"
        ) from exc
    return record


def _identifier(record: Mapping[str, Any], keys: Sequence[str], path: str) -> str:
    for key in keys:
        value = record.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value):
            return str(value)
    raise GraphInspectionError(
        "INVALID_GRAPH", f"{path} must define a non-empty id or name"
    )


def _edge_endpoints(record: Mapping[str, Any], path: str) -> tuple[str, str]:
    candidates = (
        (record.get("sourceId"), record.get("targetId")),
        (record.get("source"), record.get("target")),
        (record.get("from"), record.get("to")),
    )
    declared = record.get("declaredModels")
    if isinstance(declared, list) and len(declared) == 2:
        candidates += ((declared[0], declared[1]),)
    for source, target in candidates:
        if all(
            isinstance(value, (str, int)) and not isinstance(value, bool)
            for value in (source, target)
        ):
            return str(source), str(target)
    raise GraphInspectionError(
        "INVALID_GRAPH", f"{path} must define source and target node identifiers"
    )


def _labels(record: Mapping[str, Any], *, is_edge: bool) -> frozenset[str]:
    values: set[str] = {"EDGE" if is_edge else "NODE"}
    for key in ("type", "kind", "cardinality"):
        value = record.get(key)
        if isinstance(value, str) and value:
            values.add(value)
    if is_edge:
        values.add("RELATIONSHIP")
        name = record.get("name")
        if isinstance(name, str) and name:
            values.add(name)
    return frozenset(value.casefold() for value in values)


def _graph_kind(document: Mapping[str, Any]) -> str:
    explicit = document.get("kind") or document.get("graphType")
    if isinstance(explicit, str) and explicit:
        return explicit
    nodes = document.get("nodes") or []
    if any(
        isinstance(node, Mapping) and "type" in node and "id" in node for node in nodes
    ):
        return "ontology_graph"
    if "metricBindings" in document or "edgeSource" in document:
        return "semantic_graph"
    return "graph"


def _execute(graph: _Graph, query: _ParsedQuery, *, max_rows: int) -> dict[str, Any]:
    contexts = _match(graph, query.pattern)
    scanned_contexts = len(contexts)
    contexts = [
        context
        for context in contexts
        if all(_matches_predicate(context, predicate) for predicate in query.predicates)
    ]
    matched_contexts = len(contexts)

    projected = [(_project(context, query.returns), context) for context in contexts]
    projected.sort(key=lambda item: _json_text(item[0]))
    for order in reversed(query.order_by):
        projected.sort(
            key=lambda item, order=order: _ordering_key(
                _order_value(item[0], item[1], order.expression)
            ),
            reverse=order.descending,
        )

    effective_limit = query.limit if query.limit is not None else max_rows
    limited = projected[:effective_limit]
    rows = [row for row, _ in limited]
    explain = {
        "readOnly": True,
        "graphKind": graph.kind,
        "pattern": _explain_pattern(query.pattern),
        "filters": [
            {
                "property": predicate.property.text,
                "operator": predicate.operator,
                "value": _stable_value(predicate.value),
            }
            for predicate in query.predicates
        ],
        "returns": [
            {
                "expression": (
                    item.expression if item.expression == "*" else item.expression.text
                ),
                "alias": item.alias,
            }
            for item in query.returns
        ],
        "orderBy": [
            {
                "expression": item.expression.text,
                "direction": "DESC" if item.descending else "ASC",
            }
            for item in query.order_by
        ],
        "limit": effective_limit,
        "scanned": {
            "nodes": len(graph.nodes),
            "edges": len(graph.edges),
            "candidates": scanned_contexts,
        },
        "matched": matched_contexts,
        "returned": len(rows),
        "truncated": matched_contexts > len(rows),
    }
    return {"rows": rows, "explain": explain}


def _match(graph: _Graph, pattern: _MatchPattern) -> list[dict[str, _BoundValue]]:
    if pattern.edge is None:
        return [
            {pattern.left.variable: _bind_node(node)}
            for node in graph.nodes
            if _matches_pattern(
                _bind_node(node),
                pattern.left.label,
                pattern.left.properties,
                node.labels,
            )
        ]

    assert pattern.right is not None
    contexts: list[dict[str, _BoundValue]] = []
    for edge in graph.edges:
        if not _matches_pattern(
            _bind_edge(edge), pattern.edge.label, pattern.edge.properties, edge.labels
        ):
            continue
        if pattern.direction == "incoming":
            left_node = graph.nodes_by_id[edge.target]
            right_node = graph.nodes_by_id[edge.source]
        else:
            left_node = graph.nodes_by_id[edge.source]
            right_node = graph.nodes_by_id[edge.target]
        left = _bind_node(left_node)
        right = _bind_node(right_node)
        if not _matches_pattern(
            left, pattern.left.label, pattern.left.properties, left_node.labels
        ):
            continue
        if not _matches_pattern(
            right, pattern.right.label, pattern.right.properties, right_node.labels
        ):
            continue
        context = {pattern.left.variable: left, pattern.right.variable: right}
        if pattern.edge.variable is not None:
            context[pattern.edge.variable] = _bind_edge(edge)
        contexts.append(context)
    return contexts


def _bind_node(node: _Node) -> _BoundValue:
    return _BoundValue(
        node.record,
        {"id": node.identifier, "labels": sorted(node.labels)},
    )


def _bind_edge(edge: _Edge) -> _BoundValue:
    return _BoundValue(
        edge.record,
        {
            "id": edge.identifier,
            "sourceId": edge.source,
            "targetId": edge.target,
            "labels": sorted(edge.labels),
            "type": edge.record.get("type") or "RELATIONSHIP",
        },
    )


def _matches_pattern(
    value: _BoundValue,
    label: str | None,
    properties: Sequence[tuple[str, Any]],
    labels: frozenset[str],
) -> bool:
    if label is not None and label.casefold() not in labels:
        return False
    return all(
        _equal_values(_property(value, (name,)), expected)
        for name, expected in properties
    )


def _matches_predicate(
    context: Mapping[str, _BoundValue], predicate: _Predicate
) -> bool:
    actual = _property(context[predicate.property.variable], predicate.property.path)
    if actual is _MISSING:
        return False
    if predicate.operator == "=":
        return _equal_values(actual, predicate.value)
    expected = predicate.value
    if isinstance(actual, str) and isinstance(expected, str):
        return expected in actual
    if isinstance(actual, Sequence) and not isinstance(actual, (str, bytes)):
        return expected in actual
    return False


def _equal_values(left: Any, right: Any) -> bool:
    if left is _MISSING:
        return False
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    return left == right


def _property(value: _BoundValue, path: Sequence[str]) -> Any:
    current: Any = value.record
    for index, component in enumerate(path):
        if not isinstance(current, Mapping):
            return _MISSING
        if component in current:
            current = current[component]
            continue
        if index == 0 and component in value.virtual:
            current = value.virtual[component]
            continue
        properties = current.get("properties") if index == 0 else None
        if isinstance(properties, Mapping) and component in properties:
            current = properties[component]
            continue
        return _MISSING
    return current


def _project(
    context: Mapping[str, _BoundValue], items: Sequence[_ReturnItem]
) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for item in items:
        if item.expression == "*":
            for variable in sorted(context):
                row[variable] = _stable_value(context[variable].record)
            continue
        row[item.alias] = _stable_value(_reference_value(context, item.expression))
    return row


def _reference_value(
    context: Mapping[str, _BoundValue],
    reference: _VariableRef | _PropertyRef,
) -> Any:
    value = context[reference.variable]
    if isinstance(reference, _VariableRef):
        return value.record
    result = _property(value, reference.path)
    return None if result is _MISSING else result


def _order_value(
    row: Mapping[str, Any],
    context: Mapping[str, _BoundValue],
    expression: _VariableRef | _PropertyRef,
) -> Any:
    if isinstance(expression, _VariableRef) and expression.variable in row:
        return row[expression.variable]
    return _reference_value(context, expression)


def _ordering_key(value: Any) -> tuple[int, Any]:
    if value is None:
        return (0, "")
    if isinstance(value, bool):
        return (1, "1" if value else "0")
    if isinstance(value, (int, float)):
        return (2, value)
    if isinstance(value, str):
        return (3, value)
    return (4, _json_text(value))


def _explain_pattern(pattern: _MatchPattern) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": "NODE" if pattern.edge is None else "SINGLE_HOP",
        "direction": pattern.direction,
        "left": _explain_node(pattern.left),
    }
    if pattern.edge is not None and pattern.right is not None:
        result["edge"] = {
            "variable": pattern.edge.variable,
            "type": pattern.edge.label,
            "properties": {
                key: _stable_value(value) for key, value in pattern.edge.properties
            },
        }
        result["right"] = _explain_node(pattern.right)
    return result


def _explain_node(node: _NodePattern) -> dict[str, Any]:
    return {
        "variable": node.variable,
        "label": node.label,
        "properties": {key: _stable_value(value) for key, value in node.properties},
    }


def _stable_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _stable_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, list):
        return [_stable_value(item) for item in value]
    if isinstance(value, tuple):
        return [_stable_value(item) for item in value]
    return deepcopy(value)


def _json_text(value: Any) -> str:
    return json.dumps(
        _stable_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )

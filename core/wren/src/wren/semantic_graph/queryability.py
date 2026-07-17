"""Precompute safe metric-to-dimension reachability for the semantic graph."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from wren.semantic_graph.binding_policy import allowed_bindings


def build_queryability_index(
    semantic_graph: dict[str, Any], *, max_hops: int
) -> dict[str, Any]:
    """Build per-MetricBinding valid dimensions over bounded safe paths."""

    adjacency = _safe_adjacency(semantic_graph.get("edges") or [])
    metrics = {item["name"]: item for item in semantic_graph.get("metrics") or []}
    dimensions = {item["name"]: item for item in semantic_graph.get("dimensions") or []}
    dimension_bindings: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for binding in semantic_graph.get("dimensionBindings") or []:
        dimension_bindings[binding["dimension"]].append(binding)

    binding_entries: list[dict[str, Any]] = []
    metric_valid_sources: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    queryable_metric_bindings: list[dict[str, Any]] = []
    all_metric_bindings = semantic_graph.get("metricBindings") or []
    for metric_name, definition in metrics.items():
        queryable_metric_bindings.extend(
            allowed_bindings(
                definition,
                (
                    binding
                    for binding in all_metric_bindings
                    if binding.get("metric") == metric_name
                ),
            )
        )
    for metric_binding in queryable_metric_bindings:
        metric = metric_binding["metric"]
        source = metric_binding["model"]
        valid: list[dict[str, Any]] = []
        invalid: list[dict[str, Any]] = []
        for dimension_name in sorted(dimensions):
            definition = dimensions[dimension_name]
            master_model = definition.get("masterModel")
            candidates = allowed_bindings(
                definition, dimension_bindings.get(dimension_name, [])
            )

            paths: list[tuple[str, list[dict[str, Any]]]] = []
            for candidate in candidates:
                target = candidate["model"]
                for path in _shortest_paths(
                    adjacency, source=source, target=target, max_hops=max_hops
                ):
                    paths.append((target, path))

            if not paths:
                invalid.append(
                    {
                        "name": dimension_name,
                        "code": "MASTER_DATA_UNREACHABLE"
                        if master_model
                        else "NO_SAFE_PATH",
                        "reason": (
                            f"master model '{master_model}' is not reachable from "
                            f"'{source}' within {max_hops} safe hop(s)"
                            if master_model
                            else f"no safe MANY_TO_ONE path from '{source}' within {max_hops} hop(s)"
                        ),
                        "masterModel": master_model,
                    }
                )
                continue

            shortest = min(len(path) for _, path in paths)
            shortest_paths = [
                (target, path) for target, path in paths if len(path) == shortest
            ]
            unique: dict[tuple[Any, ...], tuple[str, list[dict[str, Any]]]] = {}
            for target, path in shortest_paths:
                signature = (
                    target,
                    *(
                        (
                            step["relationship"],
                            step["from"],
                            step["to"],
                            step.get("role"),
                        )
                        for step in path
                    ),
                )
                unique[signature] = (target, path)

            if len(unique) != 1:
                invalid.append(
                    {
                        "name": dimension_name,
                        "code": "AMBIGUOUS_SAFE_PATH",
                        "reason": f"{len(unique)} equally short safe paths are available",
                        "masterModel": master_model,
                        "candidatePaths": [
                            _public_path(target, path)
                            for target, path in sorted(
                                unique.values(),
                                key=lambda item: (
                                    item[0],
                                    [step["relationship"] for step in item[1]],
                                ),
                            )
                        ],
                    }
                )
                continue

            target, path = next(iter(unique.values()))
            entry = {
                "name": dimension_name,
                "bindingModel": target,
                "isMaster": target == master_model if master_model else False,
                "hops": len(path),
                "path": [_public_step(step) for step in path],
            }
            valid.append(entry)
            metric_valid_sources[metric][dimension_name].add(source)

        binding_entries.append(
            {
                "metricBinding": metric_binding["id"],
                "metric": metric,
                "sourceModel": source,
                "validDimensions": valid,
                "invalidDimensions": invalid,
            }
        )

    metric_entries: list[dict[str, Any]] = []
    bindings_by_metric: dict[str, list[str]] = defaultdict(list)
    for binding in binding_entries:
        bindings_by_metric[binding["metric"]].append(binding["metricBinding"])
    metric_names = sorted(
        {item["name"] for item in semantic_graph.get("metrics") or []}
    )
    for metric in metric_names:
        valid_dimensions = [
            {
                "name": dimension,
                "sourceModels": sorted(sources),
            }
            for dimension, sources in sorted(metric_valid_sources[metric].items())
        ]
        metric_entries.append(
            {
                "metric": metric,
                "metricBindings": sorted(bindings_by_metric.get(metric, [])),
                "validDimensions": valid_dimensions,
            }
        )

    binding_entries.sort(key=lambda item: (item["metric"], item["sourceModel"]))
    return {
        "schemaVersion": 1,
        "graphSchemaVersion": semantic_graph.get("schemaVersion"),
        "maxHops": max_hops,
        "edgePolicy": "VERIFIED_MANY_TO_ONE_AND_ONE_TO_ONE_ONLY",
        "metrics": metric_entries,
        "bindings": binding_entries,
    }


def find_binding_entry(
    queryability_index: dict[str, Any], *, metric: str, source_model: str
) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in queryability_index.get("bindings") or []
            if item.get("metric") == metric and item.get("sourceModel") == source_model
        ),
        None,
    )


def _safe_adjacency(
    edges: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    adjacency: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        if edge.get("cardinalityValidation") != "verified":
            continue
        for direction in edge.get("safeDirections") or []:
            if not isinstance(direction, list) or len(direction) != 2:
                continue
            source, target = direction
            adjacency[source].append(
                {
                    "relationship": edge["name"],
                    "from": source,
                    "to": target,
                    "cardinality": edge["cardinality"],
                    "condition": edge["condition"],
                    "role": edge.get("role"),
                    "entity": edge.get("entity"),
                }
            )
    for steps in adjacency.values():
        steps.sort(
            key=lambda step: (
                step["relationship"],
                step["to"],
                step.get("role") or "",
            )
        )
    return adjacency


def _shortest_paths(
    adjacency: dict[str, list[dict[str, Any]]],
    *,
    source: str,
    target: str,
    max_hops: int,
) -> list[list[dict[str, Any]]]:
    if source == target:
        return [[]]

    results: list[list[dict[str, Any]]] = []
    shortest: int | None = None

    def walk(current: str, visited: set[str], path: list[dict[str, Any]]) -> None:
        nonlocal shortest
        if len(path) >= max_hops:
            return
        if shortest is not None and len(path) >= shortest:
            return
        for step in adjacency.get(current, []):
            next_node = step["to"]
            if next_node in visited:
                continue
            next_path = [*path, step]
            if next_node == target:
                if shortest is None or len(next_path) < shortest:
                    shortest = len(next_path)
                    results.clear()
                if len(next_path) == shortest:
                    results.append(next_path)
                continue
            walk(next_node, {*visited, next_node}, next_path)

    walk(source, {source}, [])
    return results


def _public_step(step: dict[str, Any]) -> dict[str, Any]:
    return {
        "relationship": step["relationship"],
        "from": step["from"],
        "to": step["to"],
        "cardinality": step["cardinality"],
        "role": step.get("role"),
        "entity": step.get("entity"),
    }


def _public_path(target: str, path: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "bindingModel": target,
        "hops": len(path),
        "path": [_public_step(step) for step in path],
    }

"""Compile global metric and dimension definitions into graph bindings."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from wren.dimension_compiler import _atomic_fields, _global_dimension_registry
from wren.metric_compiler import _global_metric_registry, _MetricDependencyResolver
from wren.semantic_graph.config import RELATIONSHIP_FILE
from wren.semantic_graph.model import GraphConfig, GraphIssue


def compile_metrics(
    metrics: list[dict],
    nodes: dict[str, dict[str, Any]],
    config: GraphConfig,
    dialect: str | None,
    issues: list[GraphIssue],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Compile global metrics and bind them to compatible graph nodes."""

    compiler_issues = []
    definitions = _global_metric_registry(metrics, compiler_issues)
    canonical_names = {name.casefold(): name for name in definitions}
    resolver = _MetricDependencyResolver(
        definitions=definitions,
        canonical_names=canonical_names,
        global_names=canonical_names,
        dialect=dialect,
        issues=compiler_issues,
    )
    definitions_artifact: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    expansion_cache: dict[str, exp.Expression] = {}

    for name in sorted(definitions):
        definition = definitions[name]
        resolution = resolver.resolve(name)
        expanded = _expand_metric_expression(
            name,
            definitions,
            canonical_names,
            dialect,
            expansion_cache,
            [],
            issues,
        )
        data = definition.data
        master_model = _member_master_model(
            data=data,
            legacy_model=None,
            definition_path=definition.path,
            member_kind="metric",
            member_name=name,
            issues=issues,
        )
        atomic_fields = sorted(resolution.fields)
        inferred_additivity = _infer_additivity(
            data.get("expression"), resolution, dialect
        )
        policy = config.metric_policies.get(name)
        definitions_artifact.append(
            {
                "name": name,
                "expression": data.get("expression"),
                "expandedExpression": expanded.sql(dialect=dialect)
                if expanded is not None
                else None,
                "type": data.get("type"),
                "label": data.get("label"),
                "description": data.get("description"),
                "synonyms": data.get("synonyms") or [],
                "dependencies": [item for item in resolution.order if item != name],
                "atomicFields": atomic_fields,
                "masterModel": master_model,
                "additivity": policy.get("additivity", inferred_additivity)
                if policy
                else inferred_additivity,
                "blockedDimensions": policy.get("blocked_dimensions", [])
                if policy
                else [],
                "additivitySource": "configured" if policy else "inferred",
            }
        )
        for model_name, state in sorted(nodes.items()):
            if _fields_available(atomic_fields, state["field_names"]):
                bindings.append(
                    {
                        "id": f"{name}@{model_name}",
                        "metric": name,
                        "model": model_name,
                        "requiredFields": atomic_fields,
                        "grain": deepcopy(state["artifact"]["grain"]),
                        "isMaster": model_name == master_model,
                    }
                )
        metric_bindings = [binding for binding in bindings if binding["metric"] == name]
        if not metric_bindings:
            issues.append(
                GraphIssue(
                    "warning",
                    "GRAPH_METRIC_UNBOUND",
                    definition.path,
                    f"metric '{name}' cannot bind to any model/view",
                )
            )
        _validate_master_binding(
            nodes=nodes,
            bindings=metric_bindings,
            master_model=master_model,
            required_fields=atomic_fields,
            definition_path=definition.path,
            member_kind="metric",
            member_name=name,
            issues=issues,
        )
        if len(metric_bindings) > 1:
            conflicts.append(
                _binding_conflict(
                    member_kind="metric",
                    member_name=name,
                    candidates=metric_bindings,
                    master_model=master_model,
                )
            )

    for name in sorted(set(config.metric_policies) - set(definitions)):
        issues.append(
            GraphIssue(
                "error",
                "GRAPH_METRIC_POLICY_UNKNOWN",
                f"{RELATIONSHIP_FILE} > graph > metric_policies > {name}",
                "metric policy must reference a global metric name",
            )
        )

    _convert_compiler_issues(compiler_issues, issues)
    bindings.sort(key=lambda item: (item["metric"], item["model"]))
    return definitions_artifact, bindings, conflicts


def compile_dimensions(
    dimensions: list[dict],
    nodes: dict[str, dict[str, Any]],
    config: GraphConfig,
    dialect: str | None,
    issues: list[GraphIssue],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Compile global dimensions, master bindings, and conflict metadata."""

    compiler_issues = []
    definitions = _global_dimension_registry(dimensions, compiler_issues)
    definitions_artifact: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []

    for name in sorted(definitions):
        definition = definitions[name]
        fields = list(_atomic_fields(definition, dialect, compiler_issues))
        data = definition.data
        master_model = _member_master_model(
            data=data,
            legacy_model=config.master_attributes.get(name),
            definition_path=definition.path,
            member_kind="dimension",
            member_name=name,
            issues=issues,
        )
        definitions_artifact.append(
            {
                "name": name,
                "expression": data.get("expression"),
                "type": data.get("type"),
                "label": data.get("label"),
                "description": data.get("description"),
                "synonyms": data.get("synonyms") or [],
                "atomicFields": fields,
                "masterModel": master_model,
            }
        )
        for model_name, state in sorted(nodes.items()):
            if _fields_available(fields, state["field_names"]):
                bindings.append(
                    {
                        "id": f"{name}@{model_name}",
                        "dimension": name,
                        "model": model_name,
                        "requiredFields": fields,
                        "isMaster": model_name == master_model,
                    }
                )
        dimension_bindings = [
            binding for binding in bindings if binding["dimension"] == name
        ]
        if not dimension_bindings:
            issues.append(
                GraphIssue(
                    "warning",
                    "GRAPH_DIMENSION_UNBOUND",
                    definition.path,
                    f"dimension '{name}' cannot bind to any model/view",
                )
            )
        _validate_master_binding(
            nodes=nodes,
            bindings=dimension_bindings,
            master_model=master_model,
            required_fields=fields,
            definition_path=definition.path,
            member_kind="dimension",
            member_name=name,
            issues=issues,
        )

    for name in sorted(set(config.master_attributes) - set(definitions)):
        issues.append(
            GraphIssue(
                "error",
                "GRAPH_MASTER_ATTRIBUTE_UNKNOWN",
                f"{RELATIONSHIP_FILE} > graph > master_data > attributes > {name}",
                "master-data attribute must reference a global dimension name",
            )
        )

    for metric, policy in sorted(config.metric_policies.items()):
        for dimension in policy.get("blocked_dimensions", []):
            if dimension in definitions:
                continue
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_METRIC_POLICY_DIMENSION_UNKNOWN",
                    f"{RELATIONSHIP_FILE} > graph > metric_policies > {metric} > blocked_dimensions",
                    f"blocked dimension '{dimension}' is not a global dimension",
                )
            )

    _convert_compiler_issues(compiler_issues, issues)
    bindings.sort(key=lambda item: (item["dimension"], item["model"]))
    conflicts: list[dict[str, Any]] = []
    by_dimension: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for binding in bindings:
        by_dimension[binding["dimension"]].append(binding)
    for dimension, candidates in sorted(by_dimension.items()):
        if len(candidates) <= 1:
            continue
        definition = definitions[dimension]
        master = _effective_master_model(
            definition.data.get("master_model"),
            config.master_attributes.get(dimension),
        )
        conflicts.append(
            {
                "attribute": dimension,
                "candidateModels": [item["model"] for item in candidates],
                "masterModel": master,
                "resolution": "master_data" if master else "path_specific",
            }
        )
    return definitions_artifact, bindings, conflicts


def _member_master_model(
    *,
    data: dict[str, Any],
    legacy_model: str | None,
    definition_path: str,
    member_kind: str,
    member_name: str,
    issues: list[GraphIssue],
) -> str | None:
    declared = data.get("master_model")
    declared_model = (
        declared.strip() if isinstance(declared, str) and declared.strip() else None
    )
    if declared_model and legacy_model and declared_model != legacy_model:
        issues.append(
            GraphIssue(
                "error",
                "GRAPH_MASTER_MODEL_CONFLICT",
                f"{definition_path} > master_model",
                f"{member_kind} '{member_name}' declares master model "
                f"'{declared_model}', but relationships.yml declares "
                f"'{legacy_model}'",
            )
        )
    return _effective_master_model(declared_model, legacy_model)


def _effective_master_model(declared: Any, legacy_model: str | None) -> str | None:
    if isinstance(declared, str) and declared.strip():
        return declared.strip()
    return legacy_model


def _validate_master_binding(
    *,
    nodes: dict[str, dict[str, Any]],
    bindings: list[dict[str, Any]],
    master_model: str | None,
    required_fields: list[str],
    definition_path: str,
    member_kind: str,
    member_name: str,
    issues: list[GraphIssue],
) -> None:
    if master_model is None:
        return
    path = f"{definition_path} > master_model"
    if master_model not in nodes:
        issues.append(
            GraphIssue(
                "error",
                "GRAPH_MASTER_NODE_MISSING",
                path,
                f"master model '{master_model}' is not a graph node",
            )
        )
        return
    if any(binding["model"] == master_model for binding in bindings):
        return
    issues.append(
        GraphIssue(
            "error",
            "GRAPH_MASTER_BINDING_INVALID",
            path,
            f"master model '{master_model}' does not expose fields "
            f"{required_fields} required by {member_kind} '{member_name}'",
        )
    )


def _binding_conflict(
    *,
    member_kind: str,
    member_name: str,
    candidates: list[dict[str, Any]],
    master_model: str | None,
) -> dict[str, Any]:
    return {
        "kind": member_kind,
        "member": member_name,
        "candidateModels": [item["model"] for item in candidates],
        "masterModel": master_model,
        "resolution": "master_data" if master_model else "path_specific",
    }


def _expand_metric_expression(
    name: str,
    definitions: dict[str, Any],
    canonical_names: dict[str, str],
    dialect: str | None,
    cache: dict[str, exp.Expression],
    visiting: list[str],
    issues: list[GraphIssue],
) -> exp.Expression | None:
    if name in cache:
        return cache[name].copy()
    if name in visiting:
        return None
    visiting.append(name)
    expression = definitions[name].data.get("expression")
    try:
        parsed = sqlglot.parse_one(expression, dialect=dialect)
    except (ParseError, ValueError) as exc:
        issues.append(
            GraphIssue(
                "error",
                "GRAPH_METRIC_EXPRESSION_INVALID",
                definitions[name].path,
                f"cannot expand expression: {exc}",
            )
        )
        visiting.pop()
        return None

    def replace(node: exp.Expression) -> exp.Expression:
        if not isinstance(node, exp.Column) or node.table:
            return node
        dependency = canonical_names.get(node.name.casefold())
        if dependency is None or dependency == name:
            return node
        expanded = _expand_metric_expression(
            dependency,
            definitions,
            canonical_names,
            dialect,
            cache,
            visiting,
            issues,
        )
        return exp.Paren(this=expanded.copy()) if expanded is not None else node

    expanded = parsed.transform(replace, copy=True)
    visiting.pop()
    cache[name] = expanded.copy()
    return expanded


def _infer_additivity(expression: Any, resolution: Any, dialect: str | None) -> str:
    if not isinstance(expression, str):
        return "unknown"
    if len(resolution.order) > 1:
        return "non_additive"
    try:
        parsed = sqlglot.parse_one(expression, dialect=dialect)
    except (ParseError, ValueError):
        return "unknown"
    if isinstance(parsed, exp.Sum):
        return "additive"
    if isinstance(parsed, exp.Count) and parsed.find(exp.Distinct) is None:
        return "additive"
    return "non_additive"


def _fields_available(required: list[str], available: set[str]) -> bool:
    """Match semantic expression fields like the existing Cube compilers do."""

    available_folded = {field.casefold() for field in available}
    return all(field.casefold() in available_folded for field in required)


def _convert_compiler_issues(raw_issues: list[Any], issues: list[GraphIssue]) -> None:
    for issue in raw_issues:
        code = "GRAPH_SEMANTIC_MEMBER_INVALID"
        for master_code in (
            "METRIC_MASTER_MODEL_INVALID",
            "DIMENSION_MASTER_MODEL_INVALID",
        ):
            if issue.message.startswith(f"{master_code}:"):
                code = master_code
                break
        issues.append(
            GraphIssue(
                "error",
                code,
                issue.path,
                issue.message,
            )
        )

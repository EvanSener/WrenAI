"""Load graph-only policy from ``relationships.yml``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from wren.semantic_graph.model import GraphConfig, GraphIssue

GRAPH_SCHEMA_VERSION = 1
RELATIONSHIP_FILE = "relationships.yml"

_DEFAULT_MAX_HOPS = 2
_MAX_ALLOWED_HOPS = 5
_VALID_GRAPH_KEYS = {
    "bridges",
    "master_data",
    "max_hops",
    "metric_policies",
    "relationship_entities",
    "relationship_roles",
}
_VALID_ADDITIVITY = {"additive", "semi_additive", "non_additive"}
_BRIDGE_KEYS = {
    "allocation_expression",
    "allocation_mode",
    "model",
    "source_relationship",
    "target_relationship",
}
_VALID_ALLOCATION_MODES = {"custom", "proportional", "weighted"}


def load_relationship_document(
    project_path: Path, issues: list[GraphIssue]
) -> dict[str, Any]:
    """Load graph policy and relationship definitions from the shared YAML file."""

    path = project_path / RELATIONSHIP_FILE
    if not path.exists():
        issues.append(
            GraphIssue(
                "error",
                "GRAPH_RELATIONSHIP_FILE_MISSING",
                RELATIONSHIP_FILE,
                "semantic graph requires relationships.yml",
            )
        )
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        issues.append(
            GraphIssue(
                "error",
                "GRAPH_RELATIONSHIP_YAML_INVALID",
                RELATIONSHIP_FILE,
                f"cannot parse YAML: {exc}",
            )
        )
        return {}
    if not isinstance(raw, dict):
        issues.append(
            GraphIssue(
                "error",
                "GRAPH_RELATIONSHIP_DOCUMENT_INVALID",
                RELATIONSHIP_FILE,
                "top-level value must be an object",
            )
        )
        return {}
    relationships = raw.get("relationships", [])
    if not isinstance(relationships, list):
        issues.append(
            GraphIssue(
                "error",
                "GRAPH_RELATIONSHIPS_INVALID",
                f"{RELATIONSHIP_FILE} > relationships",
                "relationships must be a list",
            )
        )
    return raw


def parse_graph_config(
    raw: Any,
    issues: list[GraphIssue],
    *,
    max_hops: int | None,
) -> GraphConfig:
    """Validate the optional graph policy without changing MDL relationships."""

    path = f"{RELATIONSHIP_FILE} > graph"
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        issues.append(
            GraphIssue("error", "GRAPH_CONFIG_INVALID", path, "graph must be an object")
        )
        raw = {}

    unknown = sorted(set(raw) - _VALID_GRAPH_KEYS)
    if unknown:
        issues.append(
            GraphIssue(
                "error",
                "GRAPH_CONFIG_FIELD_UNKNOWN",
                path,
                "unsupported field(s): " + ", ".join(unknown),
            )
        )

    configured_hops = raw.get("max_hops", _DEFAULT_MAX_HOPS)
    effective_hops = max_hops if max_hops is not None else configured_hops
    if (
        not isinstance(effective_hops, int)
        or isinstance(effective_hops, bool)
        or not 0 <= effective_hops <= _MAX_ALLOWED_HOPS
    ):
        issues.append(
            GraphIssue(
                "error",
                "GRAPH_MAX_HOPS_INVALID",
                f"{path} > max_hops",
                f"max_hops must be an integer between 0 and {_MAX_ALLOWED_HOPS}",
            )
        )
        effective_hops = _DEFAULT_MAX_HOPS

    master_data = raw.get("master_data") or {}
    if not isinstance(master_data, dict):
        issues.append(
            GraphIssue(
                "error",
                "GRAPH_MASTER_DATA_INVALID",
                f"{path} > master_data",
                "master_data must be an object",
            )
        )
        master_data = {}
    unknown_master = sorted(set(master_data) - {"attributes"})
    if unknown_master:
        issues.append(
            GraphIssue(
                "error",
                "GRAPH_MASTER_DATA_FIELD_UNKNOWN",
                f"{path} > master_data",
                "unsupported field(s): " + ", ".join(unknown_master),
            )
        )

    return GraphConfig(
        max_hops=effective_hops,
        master_attributes=_string_map(
            master_data.get("attributes"),
            f"{path} > master_data > attributes",
            issues,
        ),
        relationship_roles=_string_map(
            raw.get("relationship_roles"),
            f"{path} > relationship_roles",
            issues,
        ),
        relationship_entities=_string_map(
            raw.get("relationship_entities"),
            f"{path} > relationship_entities",
            issues,
        ),
        bridge_policies=_bridge_policies(
            raw.get("bridges"), f"{path} > bridges", issues
        ),
        metric_policies=_metric_policies(
            raw.get("metric_policies"), f"{path} > metric_policies", issues
        ),
    )


def _string_map(raw: Any, path: str, issues: list[GraphIssue]) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        issues.append(
            GraphIssue(
                "error", "GRAPH_STRING_MAP_INVALID", path, "value must be an object"
            )
        )
        return {}
    result: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_STRING_MAP_KEY_INVALID",
                    path,
                    "keys must be non-empty strings",
                )
            )
            continue
        if not isinstance(value, str) or not value.strip():
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_STRING_MAP_VALUE_INVALID",
                    f"{path} > {key}",
                    "value must be a non-empty string",
                )
            )
            continue
        result[key] = value
    return result


def _bridge_policies(
    raw: Any, path: str, issues: list[GraphIssue]
) -> dict[str, dict[str, str]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        issues.append(
            GraphIssue(
                "error", "GRAPH_BRIDGES_INVALID", path, "bridges must be an object"
            )
        )
        return {}

    required = {
        "allocation_expression",
        "model",
        "source_relationship",
        "target_relationship",
    }
    result: dict[str, dict[str, str]] = {}
    for relationship, value in raw.items():
        item_path = f"{path} > {relationship}"
        if not isinstance(relationship, str) or not relationship.strip():
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_BRIDGE_RELATIONSHIP_INVALID",
                    path,
                    "bridge keys must be non-empty relationship names",
                )
            )
            continue
        if not isinstance(value, dict):
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_BRIDGE_POLICY_INVALID",
                    item_path,
                    "bridge policy must be an object",
                )
            )
            continue
        unknown = sorted(set(value) - _BRIDGE_KEYS)
        if unknown:
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_BRIDGE_FIELD_UNKNOWN",
                    item_path,
                    "unsupported field(s): " + ", ".join(unknown),
                )
            )
        missing = sorted(required - set(value))
        if missing:
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_BRIDGE_FIELD_MISSING",
                    item_path,
                    "required field(s): " + ", ".join(missing),
                )
            )
            continue
        normalized: dict[str, str] = {}
        for key in sorted(required | {"allocation_mode"}):
            field_value = value.get(
                key, "weighted" if key == "allocation_mode" else None
            )
            if not isinstance(field_value, str) or not field_value.strip():
                issues.append(
                    GraphIssue(
                        "error",
                        "GRAPH_BRIDGE_VALUE_INVALID",
                        f"{item_path} > {key}",
                        "value must be a non-empty string",
                    )
                )
                continue
            normalized[key] = field_value.strip()
        allocation_mode = normalized.get("allocation_mode")
        if allocation_mode not in _VALID_ALLOCATION_MODES:
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_BRIDGE_ALLOCATION_MODE_INVALID",
                    f"{item_path} > allocation_mode",
                    "allocation_mode must be custom, proportional, or weighted",
                )
            )
            continue
        if required.issubset(normalized):
            result[relationship] = normalized
    return result


def _metric_policies(
    raw: Any, path: str, issues: list[GraphIssue]
) -> dict[str, dict[str, Any]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        issues.append(
            GraphIssue(
                "error",
                "GRAPH_METRIC_POLICIES_INVALID",
                path,
                "metric_policies must be an object",
            )
        )
        return {}

    result: dict[str, dict[str, Any]] = {}
    for metric, value in raw.items():
        item_path = f"{path} > {metric}"
        if not isinstance(metric, str) or not metric.strip():
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_METRIC_POLICY_NAME_INVALID",
                    path,
                    "metric policy keys must be non-empty metric names",
                )
            )
            continue
        if not isinstance(value, dict):
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_METRIC_POLICY_INVALID",
                    item_path,
                    "metric policy must be an object",
                )
            )
            continue
        unknown = sorted(set(value) - {"additivity", "blocked_dimensions"})
        if unknown:
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_METRIC_POLICY_FIELD_UNKNOWN",
                    item_path,
                    "unsupported field(s): " + ", ".join(unknown),
                )
            )
        additivity = value.get("additivity")
        if additivity not in _VALID_ADDITIVITY:
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_METRIC_ADDITIVITY_INVALID",
                    f"{item_path} > additivity",
                    "additivity must be additive, semi_additive, or non_additive",
                )
            )
            continue
        blocked = value.get("blocked_dimensions", [])
        if not (
            isinstance(blocked, list)
            and all(isinstance(item, str) and item.strip() for item in blocked)
        ):
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_METRIC_BLOCKED_DIMENSIONS_INVALID",
                    f"{item_path} > blocked_dimensions",
                    "blocked_dimensions must be a list of non-empty dimension names",
                )
            )
            continue
        folded = [item.casefold() for item in blocked]
        if len(folded) != len(set(folded)):
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_METRIC_BLOCKED_DIMENSION_DUPLICATE",
                    f"{item_path} > blocked_dimensions",
                    "blocked dimension names must be unique",
                )
            )
            continue
        result[metric] = {
            "additivity": additivity,
            "blocked_dimensions": list(blocked),
        }
    return result

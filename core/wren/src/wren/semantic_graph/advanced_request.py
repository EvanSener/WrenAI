"""Normalize the structured Dynamic Virtual Cube request."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from wren.semantic_graph.advanced_calculation_inputs import (
    normalize_calculation_inputs,
)
from wren.semantic_graph.advanced_types import GraphState
from wren.semantic_graph.model import GraphPlanningError
from wren.semantic_graph.partition import normalize_graph_date_range

_REQUEST_FIELDS = {
    "anchorModel",
    "attributes",
    "calculations",
    "dateRange",
    "dimensions",
    "entityGrain",
    "fanoutMode",
    "facts",
    "includeReachable",
    "maxDepth",
    "maxHops",
    "metrics",
    "pathHints",
    "schemaVersion",
    "sourceModel",
    "targetGrain",
}


def normalize_request(request: dict[str, Any], state: GraphState) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise GraphPlanningError(
            "GRAPH_REQUEST_INVALID", "graph query request must be an object"
        )
    unknown = sorted(set(request) - _REQUEST_FIELDS)
    if unknown:
        raise GraphPlanningError(
            "GRAPH_REQUEST_FIELD_UNKNOWN",
            "graph query request contains unsupported field(s): " + ", ".join(unknown),
            details={"unknownFields": unknown},
        )
    schema_version = request.get("schemaVersion", 1)
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != 1
    ):
        raise GraphPlanningError(
            "GRAPH_REQUEST_SCHEMA_VERSION_UNSUPPORTED",
            "graph query request schemaVersion must be 1",
            details={"schemaVersion": schema_version, "supportedVersions": [1]},
        )
    anchor = request.get("anchorModel")
    if anchor is not None and (not isinstance(anchor, str) or not anchor):
        raise GraphPlanningError(
            "GRAPH_ANCHOR_INVALID", "request.anchorModel must be a non-empty string"
        )
    if isinstance(anchor, str) and anchor not in state.nodes:
        raise GraphPlanningError(
            "GRAPH_ANCHOR_NOT_FOUND",
            f"anchor model '{anchor}' is not a graph node",
            details={"anchorModel": anchor},
        )
    include_reachable = request.get("includeReachable", False)
    if not isinstance(include_reachable, bool):
        raise GraphPlanningError(
            "GRAPH_INCLUDE_REACHABLE_INVALID",
            "request.includeReachable must be a boolean",
        )
    fanout_mode = request.get("fanoutMode", "reject")
    if fanout_mode not in {"reject", "repeat"}:
        raise GraphPlanningError(
            "GRAPH_FANOUT_MODE_INVALID",
            "request.fanoutMode must be reject or repeat",
            details={"fanoutMode": fanout_mode, "allowed": ["reject", "repeat"]},
        )
    path_hints = normalize_path_hints(request.get("pathHints") or {})
    top_date_range = normalize_graph_date_range(
        request.get("dateRange"), path="request.dateRange"
    )

    raw_facts = request.get("facts")
    source_convenience = False
    if raw_facts is None and request.get("sourceModel") is not None:
        source_convenience = True
        raw_facts = [
            {
                "sourceModel": request.get("sourceModel"),
                "metrics": request.get("metrics"),
            }
        ]
    if raw_facts is None:
        raw_facts = []
    if not isinstance(raw_facts, list):
        raise GraphPlanningError("GRAPH_FACT_INVALID", "request.facts must be a list")
    facts: list[dict[str, Any]] = []
    for fact_index, raw_fact in enumerate(raw_facts):
        if not isinstance(raw_fact, dict):
            raise GraphPlanningError(
                "GRAPH_FACT_INVALID",
                f"request.facts[{fact_index}] must be an object",
            )
        source = raw_fact.get("sourceModel") or raw_fact.get("model")
        if not isinstance(source, str) or not source:
            raise GraphPlanningError(
                "GRAPH_FACT_SOURCE_REQUIRED",
                f"request.facts[{fact_index}].sourceModel is required",
            )
        metrics = normalize_members(
            raw_fact.get("metrics"),
            kind="metric",
            path=f"request.facts[{fact_index}].metrics",
        )
        if not metrics:
            raise GraphPlanningError(
                "GRAPH_FACT_METRIC_REQUIRED",
                f"fact '{source}' must request at least one metric",
            )
        fact_date_range = normalize_graph_date_range(
            raw_fact.get("dateRange", top_date_range),
            path=f"request.facts[{fact_index}].dateRange",
        )
        facts.append(
            {
                "sourceModel": source,
                "metrics": metrics,
                "dateRange": fact_date_range,
            }
        )

    raw_top_metrics = None if source_convenience else request.get("metrics")
    # Reachability controls schema discovery only.  It must never turn an
    # omitted member list into an implicit projection, otherwise asking what
    # is reachable unexpectedly joins and aggregates the graph.
    metrics_wildcard = is_wildcard(raw_top_metrics)
    top_metrics = (
        []
        if metrics_wildcard
        else normalize_members(
            raw_top_metrics or [],
            kind="metric",
            path="request.metrics",
            allow_selectors=True,
        )
    )
    raw_dimensions = request.get("dimensions")
    dimensions_wildcard = is_wildcard(raw_dimensions)
    dimensions = (
        []
        if dimensions_wildcard
        else normalize_members(
            raw_dimensions or [],
            kind="dimension",
            path="request.dimensions",
            allow_selectors=True,
        )
    )
    dimensions = [
        apply_member_hint(item, path_hints["dimensions"].get(item["name"]))
        for item in dimensions
    ]
    attributes = [
        apply_member_hint(
            item,
            path_hints["attributes"].get(f"{item['model']}.{item['field']}")
            or path_hints["attributes"].get(item["alias"]),
        )
        for item in normalize_attributes(request.get("attributes") or [])
    ]
    calculations = [
        apply_member_hint(item, path_hints["calculations"].get(item["name"]))
        for item in normalize_calculations(request.get("calculations") or [])
    ]
    for calculation in calculations:
        if calculation["stage"] == "post_aggregate" and any(
            key in calculation
            for key in ("sourceModel", "bindingModel", "relationshipPath", "role")
        ):
            raise GraphPlanningError(
                "GRAPH_POST_CALCULATION_SELECTOR_FORBIDDEN",
                f"post-aggregate calculation '{calculation['name']}' cannot select graph paths",
            )
    raw_entities = request.get("entityGrain", request.get("targetGrain", []))
    entity_grain = normalize_members(
        raw_entities or [],
        kind="entity",
        path="request.entityGrain",
        allow_selectors=True,
    )
    raw_max_depth = request.get(
        "maxDepth", request.get("maxHops", max(len(state.nodes) - 1, 0))
    )
    if isinstance(raw_max_depth, bool) or not isinstance(raw_max_depth, int):
        raise GraphPlanningError(
            "GRAPH_MAX_DEPTH_INVALID", "request.maxDepth must be an integer"
        )
    graph_limit = max(len(state.nodes) - 1, 0)
    if raw_max_depth < 0 or raw_max_depth > graph_limit:
        raise GraphPlanningError(
            "GRAPH_MAX_DEPTH_INVALID",
            f"request.maxDepth must be between 0 and {graph_limit}",
        )
    if not facts and anchor is None:
        raise GraphPlanningError(
            "GRAPH_FACT_REQUIRED", "request must provide facts or anchorModel"
        )
    if facts and (top_metrics or metrics_wildcard):
        raise GraphPlanningError(
            "GRAPH_FACT_REQUEST_CONFLICT",
            "use either request.facts or anchorModel with top-level metrics",
        )
    return {
        "schemaVersion": schema_version,
        "dateRange": top_date_range,
        "facts": facts,
        "anchorModel": anchor,
        "includeReachable": include_reachable,
        "fanoutMode": fanout_mode,
        "topMetrics": top_metrics,
        "metricsWildcard": metrics_wildcard,
        "dimensions": dimensions,
        "dimensionsWildcard": dimensions_wildcard,
        "attributes": attributes,
        "dimensionCalculations": [
            item for item in calculations if item["kind"] == "dimension"
        ],
        "metricCalculations": [
            item
            for item in calculations
            if item["kind"] == "metric" and item["stage"] == "fact_aggregate"
        ],
        "postCalculations": [
            item
            for item in calculations
            if item["kind"] == "metric" and item["stage"] == "post_aggregate"
        ],
        "entityGrain": entity_grain,
        "maxDepth": raw_max_depth,
        "pathHints": path_hints,
    }


def is_wildcard(value: Any) -> bool:
    return value == "*" or (
        isinstance(value, list) and any(item == "*" for item in value)
    )


def normalize_path_hints(raw: Any) -> dict[str, dict[str, dict[str, Any]]]:
    if not isinstance(raw, dict):
        raise GraphPlanningError(
            "GRAPH_PATH_HINTS_INVALID", "request.pathHints must be an object"
        )
    result: dict[str, dict[str, dict[str, Any]]] = {
        "dimensions": {},
        "metrics": {},
        "attributes": {},
        "calculations": {},
    }

    def add(kind: str, name: str, value: Any) -> None:
        if not isinstance(name, str) or not name:
            raise GraphPlanningError(
                "GRAPH_PATH_HINT_INVALID", "path hint member name must be non-empty"
            )
        if isinstance(value, list):
            hint = {"relationshipPath": value}
        elif isinstance(value, dict):
            hint = {}
            relationships = value.get(
                "relationshipPath", value.get("relationships", value.get("path"))
            )
            if relationships is not None:
                hint["relationshipPath"] = relationships
            for key in ("bindingModel", "sourceModel", "role"):
                if key in value:
                    hint[key] = value[key]
        else:
            raise GraphPlanningError(
                "GRAPH_PATH_HINT_INVALID",
                f"path hint for '{name}' must be a relationship list or object",
            )
        relationships = hint.get("relationshipPath")
        if relationships is not None and (
            not isinstance(relationships, list)
            or not all(isinstance(item, str) and item for item in relationships)
        ):
            raise GraphPlanningError(
                "GRAPH_PATH_HINT_INVALID",
                f"path hint for '{name}' has an invalid relationship path",
            )
        result[kind][name] = hint

    for plural in ("dimensions", "metrics", "attributes", "calculations"):
        nested = raw.get(plural)
        if nested is None:
            continue
        if not isinstance(nested, dict):
            raise GraphPlanningError(
                "GRAPH_PATH_HINTS_INVALID", f"pathHints.{plural} must be an object"
            )
        for name, value in nested.items():
            add(plural, name, value)
    prefixes = {
        "dimension:": "dimensions",
        "metric:": "metrics",
        "attribute:": "attributes",
        "calculation:": "calculations",
    }
    for key, value in raw.items():
        if key in result:
            continue
        matched = next((item for item in prefixes if key.startswith(item)), None)
        if matched:
            add(prefixes[matched], key.removeprefix(matched), value)
        else:
            add("dimensions", key, value)
    return result


def normalize_attributes(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise GraphPlanningError(
            "GRAPH_ATTRIBUTES_INVALID", "request.attributes must be a list"
        )
    result: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if isinstance(item, str):
            model, separator, field = item.partition(".")
            if not separator:
                model = ""
            value: dict[str, Any] = {"model": model, "field": field, "alias": field}
        elif isinstance(item, dict):
            model = item.get("model")
            field = item.get("field")
            value = {
                "model": model,
                "field": field,
                "alias": item.get("alias", field),
            }
            for key in ("relationshipPath", "role"):
                if key in item:
                    value[key] = deepcopy(item[key])
            if decision := _explicit_route_decision(item):
                value["_routeDecision"] = decision
        else:
            raise GraphPlanningError(
                "GRAPH_ATTRIBUTE_INVALID",
                f"request.attributes[{index}] must be model.field or an object",
            )
        for key, code in (
            ("model", "GRAPH_ATTRIBUTE_MODEL_REQUIRED"),
            ("field", "GRAPH_ATTRIBUTE_FIELD_REQUIRED"),
            ("alias", "GRAPH_ATTRIBUTE_ALIAS_INVALID"),
        ):
            if not isinstance(value[key], str) or not value[key]:
                raise GraphPlanningError(
                    code, f"request.attributes[{index}].{key} is required"
                )
        result.append(value)
    return result


def normalize_calculations(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise GraphPlanningError(
            "GRAPH_CALCULATIONS_INVALID", "request.calculations must be a list"
        )
    result: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise GraphPlanningError(
                "GRAPH_CALCULATION_INVALID",
                f"request.calculations[{index}] must be an object",
            )
        name = item.get("name")
        raw_kind = str(item.get("kind") or "").lower()
        kind = "metric" if raw_kind == "post_metric" else raw_kind
        expression = item.get("expression")
        raw_stage = item.get("stage")
        if raw_stage is not None and not isinstance(raw_stage, str):
            raise GraphPlanningError(
                "GRAPH_CALCULATION_STAGE_INVALID",
                f"calculation '{name}' stage must be a string",
            )
        if isinstance(raw_stage, str):
            raw_stage = raw_stage.casefold()
        if not isinstance(name, str) or not name:
            raise GraphPlanningError(
                "GRAPH_CALCULATION_NAME_REQUIRED",
                f"request.calculations[{index}].name is required",
            )
        if kind not in {"dimension", "metric"}:
            raise GraphPlanningError(
                "GRAPH_CALCULATION_KIND_INVALID",
                f"calculation '{name}' kind must be dimension, metric, or post_metric",
            )
        if not isinstance(expression, str) or not expression.strip():
            raise GraphPlanningError(
                "GRAPH_CALCULATION_EXPRESSION_REQUIRED",
                f"calculation '{name}' expression is required",
            )
        value = {
            "name": name,
            "alias": item.get("alias", name),
            "kind": kind,
            "expression": expression,
        }
        if kind == "dimension":
            if raw_stage not in {None, "row", "row_level"}:
                raise GraphPlanningError(
                    "GRAPH_CALCULATION_STAGE_INVALID",
                    f"dimension calculation '{name}' stage must be row",
                )
            value["stage"] = "row"
        else:
            stage = "post_aggregate" if raw_kind == "post_metric" else raw_stage
            aliases = {
                None: "fact_aggregate",
                "fact": "fact_aggregate",
                "aggregate": "fact_aggregate",
                "fact_aggregate": "fact_aggregate",
                "post": "post_aggregate",
                "post_aggregate": "post_aggregate",
            }
            if stage not in aliases:
                raise GraphPlanningError(
                    "GRAPH_CALCULATION_STAGE_INVALID",
                    f"metric calculation '{name}' stage must be fact_aggregate or post_aggregate",
                )
            value["stage"] = aliases[stage]
        for key in ("sourceModel", "bindingModel", "relationshipPath", "role"):
            if key in item:
                value[key] = deepcopy(item[key])
        if decision := _explicit_route_decision(item):
            value["_routeDecision"] = decision
        if value["stage"] == "post_aggregate" and any(
            key in value
            for key in ("sourceModel", "bindingModel", "relationshipPath", "role")
        ):
            raise GraphPlanningError(
                "GRAPH_POST_CALCULATION_SELECTOR_FORBIDDEN",
                f"post-aggregate calculation '{name}' cannot select graph paths",
            )
        if "inputs" in item:
            if value["stage"] == "post_aggregate":
                raise GraphPlanningError(
                    "GRAPH_POST_CALCULATION_INPUTS_FORBIDDEN",
                    f"post-aggregate calculation '{name}' references output aliases, not graph inputs",
                )
            value["inputs"] = normalize_calculation_inputs(
                item["inputs"], calculation_name=name
            )
        if not isinstance(value["alias"], str) or not value["alias"]:
            raise GraphPlanningError(
                "GRAPH_CALCULATION_ALIAS_INVALID",
                f"calculation '{name}' alias must be non-empty",
            )
        result.append(value)
    return result


def apply_member_hint(
    member: dict[str, Any], hint: dict[str, Any] | None
) -> dict[str, Any]:
    result = deepcopy(member)
    contributed = {key for key in (hint or {}) if key not in result}
    for key, value in (hint or {}).items():
        result.setdefault(key, deepcopy(value))
    if contributed and "relationshipPath" not in member:
        result["_routeDecision"] = "pathHint"
    return result


def _explicit_route_decision(selector: dict[str, Any]) -> str | None:
    """Retain selector provenance internally for truthful graph explain output."""

    if "relationshipPath" in selector:
        return "explicitRelationshipPath"
    if "role" in selector:
        return "roleResolved"
    if "bindingModel" in selector:
        return "explicitBindingModel"
    if "sourceModel" in selector:
        return "explicitSourceModel"
    return None


def normalize_members(
    raw_members: Any,
    *,
    kind: str,
    path: str,
    allow_selectors: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(raw_members, list):
        raise GraphPlanningError(
            f"GRAPH_{kind.upper()}_LIST_INVALID", f"{path} must be a list"
        )
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for index, raw in enumerate(raw_members):
        if isinstance(raw, str):
            item: dict[str, Any] = {"name": raw, "alias": raw}
        elif isinstance(raw, dict):
            name = raw.get("name")
            item = {"name": name, "alias": raw.get("alias", name)}
            if allow_selectors:
                for key in (
                    "bindingModel",
                    "sourceModel",
                    "relationshipPath",
                    "role",
                ):
                    if key in raw:
                        item[key] = deepcopy(raw[key])
                if decision := _explicit_route_decision(raw):
                    item["_routeDecision"] = decision
        else:
            raise GraphPlanningError(
                f"GRAPH_{kind.upper()}_INVALID",
                f"{path}[{index}] must be a name or object",
            )
        if not isinstance(item.get("name"), str) or not item["name"]:
            raise GraphPlanningError(
                f"GRAPH_{kind.upper()}_NAME_REQUIRED",
                f"{path}[{index}].name is required",
            )
        if not isinstance(item.get("alias"), str) or not item["alias"]:
            raise GraphPlanningError(
                f"GRAPH_{kind.upper()}_ALIAS_INVALID",
                f"{path}[{index}].alias must be a non-empty string",
            )
        relationship_path = item.get("relationshipPath")
        if relationship_path is not None and (
            not isinstance(relationship_path, list)
            or not all(isinstance(value, str) and value for value in relationship_path)
        ):
            raise GraphPlanningError(
                "GRAPH_RELATIONSHIP_PATH_INVALID",
                f"{path}[{index}].relationshipPath must be a list of relationship names",
            )
        for selector in ("bindingModel", "sourceModel", "role"):
            if selector in item and (
                not isinstance(item[selector], str) or not item[selector]
            ):
                raise GraphPlanningError(
                    f"GRAPH_{selector.upper()}_INVALID",
                    f"{path}[{index}].{selector} must be a non-empty string",
                )
        signature = (
            item["name"],
            item["alias"],
            item.get("bindingModel"),
            item.get("sourceModel"),
            tuple(item.get("relationshipPath") or []),
            item.get("role"),
        )
        if signature not in seen:
            seen.add(signature)
            result.append(item)
    return result

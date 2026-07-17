"""Normalize and resolve declared calculation fields across graph nodes.

The helpers in this module intentionally do not decide whether a calculation is
a dimension or a metric.  They validate field declarations and graph routes so
row-level dimensions can use them now and aggregate calculations can reuse the
same contract later.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlglot import exp

from wren.semantic_graph.advanced_member_routes import route_signature
from wren.semantic_graph.advanced_traversal import public_step, resolve_node_path
from wren.semantic_graph.advanced_types import GraphState
from wren.semantic_graph.model import GraphPlanningError


def normalize_calculation_inputs(
    raw: Any, *, calculation_name: str
) -> list[dict[str, Any]]:
    """Normalize ``calculations[].inputs`` into explicit model-field selectors."""

    if not isinstance(raw, list):
        raise GraphPlanningError(
            "GRAPH_CALCULATION_INPUTS_INVALID",
            f"calculation '{calculation_name}' inputs must be a list",
        )
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise GraphPlanningError(
                "GRAPH_CALCULATION_INPUT_INVALID",
                f"calculation '{calculation_name}' inputs[{index}] must be an object",
            )
        value = {"model": item.get("model"), "field": item.get("field")}
        for key in ("relationshipPath", "role"):
            if key in item:
                value[key] = deepcopy(item[key])
        for key in ("model", "field"):
            if not isinstance(value[key], str) or not value[key]:
                raise GraphPlanningError(
                    "GRAPH_CALCULATION_INPUT_INVALID",
                    f"calculation '{calculation_name}' inputs[{index}].{key} is required",
                )
        relationship_path = value.get("relationshipPath")
        if relationship_path is not None and (
            not isinstance(relationship_path, list)
            or not all(
                isinstance(relationship, str) and relationship
                for relationship in relationship_path
            )
        ):
            raise GraphPlanningError(
                "GRAPH_CALCULATION_INPUT_PATH_INVALID",
                f"calculation '{calculation_name}' inputs[{index}].relationshipPath "
                "must be a list of relationship names",
            )
        role = value.get("role")
        if role is not None and (not isinstance(role, str) or not role):
            raise GraphPlanningError(
                "GRAPH_CALCULATION_INPUT_ROLE_INVALID",
                f"calculation '{calculation_name}' inputs[{index}].role must be "
                "a non-empty string",
            )
        signature = (
            value["model"],
            value["field"],
            tuple(relationship_path or []),
            role,
        )
        if signature not in seen:
            seen.add(signature)
            result.append(value)
    return result


def resolve_calculation_inputs(
    state: GraphState,
    adjacency: dict[str, list[dict[str, Any]]],
    *,
    source: str,
    request: dict[str, Any],
    parsed: exp.Expression,
    max_depth: int,
) -> dict[str, Any]:
    """Validate expression fields and resolve each declared input independently."""

    if "inputs" not in request:
        return _resolve_legacy_inputs(
            state,
            adjacency,
            source=source,
            request=request,
            parsed=parsed,
            max_depth=max_depth,
        )
    return _resolve_declared_inputs(
        state,
        adjacency,
        source=source,
        request=request,
        parsed=parsed,
        max_depth=max_depth,
    )


def _resolve_declared_inputs(
    state: GraphState,
    adjacency: dict[str, list[dict[str, Any]]],
    *,
    source: str,
    request: dict[str, Any],
    parsed: exp.Expression,
    max_depth: int,
) -> dict[str, Any]:
    name = request["name"]
    inputs = request["inputs"]
    columns = list(parsed.find_all(exp.Column))
    malformed = sorted(
        {
            column.sql(dialect=state.dialect)
            for column in columns
            if not column.table or column.db or column.catalog
        }
    )
    if malformed:
        raise GraphPlanningError(
            "GRAPH_CALCULATION_INPUT_QUALIFICATION_REQUIRED",
            f"calculation '{name}' inputs require qualified model.field references",
            details={"fields": malformed},
        )

    declared: dict[tuple[str, str], dict[str, Any]] = {}
    for item in inputs:
        model = item["model"]
        field = item["field"]
        _validate_model_field(state, name=name, model=model, field=field)
        key = (model.casefold(), field.casefold())
        previous = declared.get(key)
        if previous is not None and _selector(previous) != _selector(item):
            raise GraphPlanningError(
                "GRAPH_CALCULATION_INPUT_ROUTE_CONFLICT",
                f"calculation '{name}' declares multiple routes for '{model}.{field}'",
                details={"model": model, "field": field},
            )
        declared[key] = item

    referenced = {
        (column.table.casefold(), column.name.casefold()) for column in columns
    }
    undeclared = sorted(
        column.sql(dialect=state.dialect)
        for column in columns
        if (column.table.casefold(), column.name.casefold()) not in declared
    )
    if undeclared:
        raise GraphPlanningError(
            "GRAPH_CALCULATION_INPUT_UNDECLARED",
            f"calculation '{name}' references fields not declared in inputs",
            details={"fields": undeclared},
        )
    unused = sorted(
        f"{item['model']}.{item['field']}"
        for key, item in declared.items()
        if key not in referenced
    )
    if unused:
        raise GraphPlanningError(
            "GRAPH_CALCULATION_INPUT_UNUSED",
            f"calculation '{name}' declares inputs that its expression does not use",
            details={"fields": unused},
        )

    resolved_inputs: list[dict[str, Any]] = []
    routes_by_model: dict[str, dict[str, Any]] = {}
    for item in inputs:
        source_input = item["model"] == source
        relationship_path = item.get(
            "relationshipPath",
            None if source_input else request.get("relationshipPath"),
        )
        role = item.get("role", None if source_input else request.get("role"))
        path = resolve_node_path(
            state,
            adjacency,
            source=source,
            target=item["model"],
            relationship_path=relationship_path,
            role=role,
            max_depth=max_depth,
            member=f"{name}:{item['model']}.{item['field']}",
        )
        if (
            role is not None
            and relationship_path is not None
            and not any(step.get("role") == role for step in path)
        ):
            raise GraphPlanningError(
                "GRAPH_CALCULATION_INPUT_ROLE_MISMATCH",
                f"calculation '{name}' input '{item['model']}.{item['field']}' "
                f"does not traverse role '{role}'",
                details={
                    "model": item["model"],
                    "field": item["field"],
                    "role": role,
                    "relationshipPath": relationship_path,
                },
            )
        public_path = [public_step(step) for step in path]
        resolved = {
            **deepcopy(item),
            "path": public_path,
            "hops": len(public_path),
        }
        resolved_inputs.append(resolved)
        model_key = item["model"].casefold()
        route = {
            "model": item["model"],
            "path": public_path,
            "hops": len(public_path),
            "fields": [item["field"]],
        }
        previous = routes_by_model.get(model_key)
        if previous is not None:
            if route_signature(previous["path"]) != route_signature(public_path):
                raise GraphPlanningError(
                    "GRAPH_CALCULATION_MODEL_ROUTE_AMBIGUOUS",
                    f"calculation '{name}' reaches model '{item['model']}' by "
                    "multiple routes",
                    details={
                        "model": item["model"],
                        "routes": [previous["path"], public_path],
                    },
                )
            if item["field"] not in previous["fields"]:
                previous["fields"].append(item["field"])
        else:
            routes_by_model[model_key] = route

    routes = list(routes_by_model.values())
    models = [route["model"] for route in routes]
    return {
        "inputs": resolved_inputs,
        "routes": routes,
        "bindingModel": models[0] if len(models) == 1 else None,
        "bindingModels": models,
        "defaultModel": source,
        "path": routes[0]["path"] if len(routes) == 1 else [],
        "hops": max((route["hops"] for route in routes), default=0),
        "inputsDeclared": True,
    }


def _resolve_legacy_inputs(
    state: GraphState,
    adjacency: dict[str, list[dict[str, Any]]],
    *,
    source: str,
    request: dict[str, Any],
    parsed: exp.Expression,
    max_depth: int,
) -> dict[str, Any]:
    name = request["name"]
    columns = list(parsed.find_all(exp.Column))
    qualified_models = {column.table for column in columns if column.table}
    requested_model = request.get("bindingModel") or request.get("sourceModel")
    if requested_model:
        qualified_models.add(requested_model)
    if len(qualified_models) > 1:
        raise GraphPlanningError(
            "GRAPH_DIMENSION_CALCULATION_MULTI_NODE_UNSAFE",
            f"dimension calculation '{name}' spans multiple nodes",
            details={"models": sorted(qualified_models)},
        )
    target = next(iter(qualified_models), source)
    _validate_model(state, name=name, model=target)
    invalid = sorted(
        {
            column.sql(dialect=state.dialect)
            for column in columns
            if not _field_exists(state, target, column.name)
            or (column.table and column.table != target)
        }
    )
    if invalid:
        raise GraphPlanningError(
            "GRAPH_CALCULATION_FIELD_UNREACHABLE",
            f"dimension calculation '{name}' references invalid fields",
            details={"fields": invalid, "bindingModel": target},
        )
    path = resolve_node_path(
        state,
        adjacency,
        source=source,
        target=target,
        relationship_path=request.get("relationshipPath"),
        role=request.get("role"),
        max_depth=max_depth,
        member=name,
    )
    public_path = [public_step(step) for step in path]
    fields = list(dict.fromkeys(column.name for column in columns))
    return {
        "inputs": [
            {
                "model": target,
                "field": field,
                "path": deepcopy(public_path),
                "hops": len(public_path),
                "inferred": True,
            }
            for field in fields
        ],
        "routes": [
            {
                "model": target,
                "path": public_path,
                "hops": len(public_path),
                "fields": fields,
            }
        ],
        "bindingModel": target,
        "bindingModels": [target],
        "defaultModel": target,
        "path": public_path,
        "hops": len(public_path),
        "inputsDeclared": False,
    }


def _validate_model_field(
    state: GraphState, *, name: str, model: str, field: str
) -> None:
    _validate_model(state, name=name, model=model)
    if not _field_exists(state, model, field):
        raise GraphPlanningError(
            "GRAPH_CALCULATION_FIELD_UNREACHABLE",
            f"calculation '{name}' declares an unknown field '{model}.{field}'",
            details={"model": model, "field": field},
        )


def _validate_model(state: GraphState, *, name: str, model: str) -> None:
    if model not in state.nodes:
        raise GraphPlanningError(
            "GRAPH_CALCULATION_MODEL_NOT_FOUND",
            f"calculation '{name}' references unknown node '{model}'",
        )


def _field_exists(state: GraphState, model: str, field: str) -> bool:
    return any(
        isinstance(attribute, dict)
        and isinstance(attribute.get("name"), str)
        and attribute["name"].casefold() == field.casefold()
        for attribute in state.nodes[model].get("attributes") or []
    )


def _selector(item: dict[str, Any]) -> tuple[Any, ...]:
    return (tuple(item.get("relationshipPath") or []), item.get("role"))

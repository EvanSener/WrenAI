"""Resolve semantic dimensions, raw attributes, and dimension calculations."""

from __future__ import annotations

from typing import Any

from sqlglot import exp

from wren.semantic_graph.advanced_bridge import normalized_bridge_policy
from wren.semantic_graph.advanced_calculation_inputs import (
    resolve_calculation_inputs,
)
from wren.semantic_graph.advanced_expression import parse_calculation_expression
from wren.semantic_graph.advanced_traversal import (
    enumerate_paths,
    path_from_hint,
    public_step,
    reject_unprotected_m2m_hint,
    resolve_node_path,
    unprotected_many_to_many_edges,
)
from wren.semantic_graph.advanced_types import GraphState
from wren.semantic_graph.binding_policy import (
    allowed_bindings,
    enforce_master_model,
    master_model,
    source_equivalent_dimension_binding,
)
from wren.semantic_graph.model import GraphPlanningError


def resolve_dimension(
    state: GraphState,
    adjacency: dict[str, list[dict[str, Any]]],
    *,
    source: str,
    request: dict[str, Any],
    max_depth: int,
) -> dict[str, Any]:
    name = request["name"]
    definition = state.dimensions.get(name)
    if definition is None:
        raise GraphPlanningError(
            "GRAPH_DIMENSION_NOT_FOUND",
            f"dimension '{name}' is not defined",
            details={"dimension": name},
        )
    all_bindings = [
        item for item in state.dimension_bindings if item.get("dimension") == name
    ]
    candidates = allowed_bindings(definition, all_bindings)
    master = master_model(definition)
    requested_model = request.get("bindingModel")
    source_equivalent = source_equivalent_dimension_binding(
        definition,
        all_bindings,
        state.edges.values(),
        source_model=source,
    )
    if master:
        if requested_model:
            if (
                source_equivalent is not None
                and requested_model == source_equivalent.get("model")
            ):
                candidates = [source_equivalent]
            else:
                enforce_master_model(
                    member_kind="dimension",
                    member_name=name,
                    definition=definition,
                    requested_model=requested_model,
                )
    elif requested_model:
        candidates = [
            item for item in candidates if item.get("model") == requested_model
        ]
    if not candidates:
        raise GraphPlanningError(
            "GRAPH_DIMENSION_BINDING_NOT_FOUND",
            f"dimension '{name}' has no permitted binding",
            details={
                "dimension": name,
                "masterModel": master,
                "requestedModel": requested_model,
            },
        )

    relationship_path = request.get("relationshipPath")
    role = request.get("role")
    paths: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    if relationship_path is not None:
        reject_unprotected_m2m_hint(state, relationship_path)
        hinted_path = path_from_hint(
            adjacency,
            source=source,
            relationships=relationship_path,
            max_depth=max_depth,
        )
        target = hinted_path[-1]["to"] if hinted_path else source
        paths = [
            (binding, hinted_path)
            for binding in candidates
            if binding["model"] == target
            and (role is None or any(step.get("role") == role for step in hinted_path))
        ]
    else:
        for binding in candidates:
            remaining = 2 - len(paths)
            if remaining <= 0:
                break
            paths.extend(
                (binding, path)
                for path in enumerate_paths(
                    adjacency,
                    source=source,
                    target=binding["model"],
                    max_depth=max_depth,
                    limit=remaining,
                    required_role=role,
                )
            )

    if not paths:
        unprotected = unprotected_many_to_many_edges(
            state, source, [item["model"] for item in candidates]
        )
        if unprotected:
            raise GraphPlanningError(
                "GRAPH_MANY_TO_MANY_POLICY_REQUIRED",
                f"dimension '{name}' requires a governed Bridge/Allocation policy",
                details={
                    "dimension": name,
                    "sourceModel": source,
                    "relationships": unprotected,
                },
            )
        raise GraphPlanningError(
            "GRAPH_DIMENSION_PATH_NOT_FOUND",
            f"dimension '{name}' is not reachable from '{source}' within depth {max_depth}",
            details={
                "dimension": name,
                "sourceModel": source,
                "bindingModels": sorted(item["model"] for item in candidates),
                "maxDepth": max_depth,
                "relationshipPath": relationship_path,
                "role": role,
            },
        )

    unique: dict[tuple[Any, ...], tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    for binding, path in paths:
        signature = (
            binding["model"],
            tuple(
                (
                    step["relationship"],
                    step["from"],
                    step["to"],
                    step.get("role"),
                )
                for step in path
            ),
        )
        unique[signature] = (binding, path)
    if len(unique) != 1:
        raise GraphPlanningError(
            "GRAPH_PATH_AMBIGUOUS",
            f"dimension '{name}' has multiple paths from '{source}'",
            details={
                "dimension": name,
                "sourceModel": source,
                "candidatePaths": [
                    {
                        "bindingModel": binding["model"],
                        "path": [public_step(step) for step in path],
                    }
                    for binding, path in sorted(
                        unique.values(),
                        key=lambda item: (
                            item[0]["model"],
                            [step["relationship"] for step in item[1]],
                        ),
                    )
                ],
                "hint": "set request.pathHints or bindingModel/relationshipPath/role",
            },
        )
    binding, path = next(iter(unique.values()))
    for step in path:
        if step["traversal"] == "BRIDGE":
            normalized_bridge_policy(state, step)
    if any(step["traversal"] != "SAFE" for step in path):
        unprotected = unprotected_many_to_many_edges(state, source, [binding["model"]])
        if unprotected:
            raise GraphPlanningError(
                "GRAPH_MANY_TO_MANY_POLICY_REQUIRED",
                f"dimension '{name}' crosses an ungoverned many-to-many relationship",
                details={"relationships": unprotected, "dimension": name},
            )

    expression = definition.get("expression")
    if not isinstance(expression, str) or not expression.strip():
        raise GraphPlanningError(
            "GRAPH_DIMENSION_EXPRESSION_MISSING",
            f"dimension '{name}' has no compiled expression",
        )
    return {
        "name": name,
        "alias": request["alias"],
        "memberKind": "dimension",
        "expression": expression,
        "bindingModel": binding["model"],
        "binding": binding.get("id"),
        "isMaster": bool(master and binding["model"] == master),
        "type": definition.get("type"),
        "path": [public_step(step) for step in path],
        "hops": len(path),
        "routeDecision": (
            "sourceEquivalentMasterKey"
            if source_equivalent is not None
            and binding["model"] == source_equivalent.get("model")
            else request.get("_routeDecision")
            or ("masterDataBinding" if master else "uniqueSafePath")
        ),
    }


def resolve_attribute(
    state: GraphState,
    adjacency: dict[str, list[dict[str, Any]]],
    *,
    source: str,
    request: dict[str, Any],
    max_depth: int,
) -> dict[str, Any]:
    model = request["model"]
    node = state.nodes.get(model)
    if node is None:
        raise GraphPlanningError(
            "GRAPH_ATTRIBUTE_MODEL_NOT_FOUND",
            f"attribute model '{model}' is not a graph node",
            details={"model": model, "field": request["field"]},
        )
    attribute = next(
        (
            item
            for item in node.get("attributes") or []
            if item.get("name") == request["field"]
        ),
        None,
    )
    if attribute is None:
        raise GraphPlanningError(
            "GRAPH_ATTRIBUTE_FIELD_NOT_FOUND",
            f"node '{model}' has no attribute '{request['field']}'",
            details={"model": model, "field": request["field"]},
        )
    path = resolve_node_path(
        state,
        adjacency,
        source=source,
        target=model,
        relationship_path=request.get("relationshipPath"),
        role=request.get("role"),
        max_depth=max_depth,
        member=f"{model}.{request['field']}",
    )
    return {
        "name": f"{model}.{request['field']}",
        "alias": request["alias"],
        "memberKind": "attribute",
        "expression": f"{model}.{request['field']}",
        "bindingModel": model,
        "binding": f"attribute:{model}.{request['field']}",
        "isMaster": False,
        "type": attribute.get("type"),
        "path": [public_step(step) for step in path],
        "hops": len(path),
        "routeDecision": request.get("_routeDecision") or "uniqueSafePath",
    }


def resolve_dimension_calculation(
    state: GraphState,
    adjacency: dict[str, list[dict[str, Any]]],
    *,
    source: str,
    request: dict[str, Any],
    max_depth: int,
) -> dict[str, Any]:
    parsed = parse_calculation_expression(
        request["expression"],
        dialect=state.dialect,
        name=request["name"],
    )
    if parsed.find(exp.AggFunc) is not None:
        raise GraphPlanningError(
            "GRAPH_DIMENSION_CALCULATION_AGGREGATE_FORBIDDEN",
            f"dimension calculation '{request['name']}' cannot contain aggregates",
        )
    columns = list(parsed.find_all(exp.Column))
    if any(not column.table and column.name in state.metrics for column in columns):
        raise GraphPlanningError(
            "GRAPH_DIMENSION_CALCULATION_METRIC_FORBIDDEN",
            f"dimension calculation '{request['name']}' cannot reference a global metric",
        )
    input_plan = resolve_calculation_inputs(
        state,
        adjacency,
        source=source,
        request=request,
        parsed=parsed,
        max_depth=max_depth,
    )
    binding_target = input_plan["bindingModel"] or source
    return {
        "name": request["name"],
        "alias": request["alias"],
        "memberKind": "calculation",
        "expression": parsed.sql(dialect=state.dialect),
        "binding": f"calculation:{request['name']}@{binding_target}",
        "isMaster": False,
        "type": None,
        **input_plan,
        "routeDecision": request.get("_routeDecision") or "uniqueSafePath",
    }

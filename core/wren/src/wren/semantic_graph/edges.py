"""Compile ``relationships.yml`` entries into safe semantic graph edges."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlglot import exp

from wren.semantic_graph.advanced_expression import parse_calculation_expression
from wren.semantic_graph.config import RELATIONSHIP_FILE
from wren.semantic_graph.edge_bridges import validate_bridge_policies
from wren.semantic_graph.model import GraphConfig, GraphIssue, GraphPlanningError

_JOIN_TYPES = {
    "MANY_TO_ONE",
    "ONE_TO_MANY",
    "ONE_TO_ONE",
    "MANY_TO_MANY",
}


def compile_edges(
    raw_relationships: Any,
    nodes: dict[str, dict[str, Any]],
    config: GraphConfig,
    dialect: str | None,
    issues: list[GraphIssue],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Compile relationship directions, entities, roles, and cardinality state."""

    if not isinstance(raw_relationships, list):
        return [], {}

    known_names: set[str] = set()
    records: list[dict[str, Any]] = []
    pair_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    node_entities: dict[str, list[dict[str, Any]]] = defaultdict(list)
    master_model_entities: dict[str, list[str]] = defaultdict(list)
    for attribute, model in config.master_attributes.items():
        master_model_entities[model].append(attribute)

    for index, raw in enumerate(raw_relationships):
        path = f"{RELATIONSHIP_FILE} > relationships[{index}]"
        if not isinstance(raw, dict):
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_RELATIONSHIP_INVALID",
                    path,
                    "relationship must be an object",
                )
            )
            continue
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_RELATIONSHIP_NAME_MISSING",
                    path,
                    "relationship must define a name",
                )
            )
            continue
        path = f"{RELATIONSHIP_FILE} > {name}"
        if name in known_names:
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_RELATIONSHIP_DUPLICATE",
                    path,
                    f"relationship name '{name}' is already defined",
                )
            )
            continue
        known_names.add(name)

        models = raw.get("models")
        if (
            not isinstance(models, list)
            or len(models) != 2
            or not all(isinstance(model, str) and model for model in models)
            or models[0] == models[1]
        ):
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_RELATIONSHIP_MODELS_INVALID",
                    path,
                    "models must contain exactly two distinct model/view names",
                )
            )
            continue
        missing = [model for model in models if model not in nodes]
        if missing:
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_RELATIONSHIP_NODE_MISSING",
                    path,
                    "unknown node(s): " + ", ".join(missing),
                )
            )
            continue

        join_type = str(raw.get("join_type") or "").upper()
        if join_type not in _JOIN_TYPES:
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_CARDINALITY_INVALID",
                    path,
                    f"join_type must be one of {sorted(_JOIN_TYPES)}",
                )
            )
            continue

        condition = raw.get("condition")
        if not isinstance(condition, str) or not condition.strip():
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_RELATIONSHIP_CONDITION_MISSING",
                    path,
                    "condition must be a non-empty string",
                )
            )
            continue
        condition_columns = _condition_columns(
            condition, tuple(models), dialect, path, issues
        )
        _validate_condition_fields(
            tuple(models), condition_columns, nodes, path, issues
        )

        declared_directions = _safe_directions(tuple(models), join_type)
        role = config.relationship_roles.get(name)
        one_side = _one_side(tuple(models), join_type)
        entity = config.relationship_entities.get(name)
        if entity is None and one_side is not None:
            candidates = sorted(master_model_entities.get(one_side, []))
            entity = candidates[0] if candidates else one_side
        if entity is None:
            entity = role or name

        cardinality_status = _validate_cardinality(
            name=name,
            models=tuple(models),
            join_type=join_type,
            bridge_configured=name in config.bridge_policies,
            condition_columns=condition_columns,
            nodes=nodes,
            path=path,
            issues=issues,
        )
        safe_directions = (
            declared_directions if cardinality_status == "verified" else ()
        )
        record = {
            "name": name,
            "declaredModels": list(models),
            "cardinality": join_type,
            "condition": condition,
            "conditionColumns": {
                model: condition_columns.get(model, []) for model in models
            },
            "declaredDirections": [
                list(direction) for direction in declared_directions
            ],
            "safeDirections": [list(direction) for direction in safe_directions],
            "role": role,
            "entity": entity,
            "cardinalityValidation": cardinality_status,
        }
        records.append(record)
        pair_groups[tuple(sorted(models))].append(record)
        _add_relationship_entities(
            node_entities,
            tuple(models),
            join_type,
            condition_columns,
            entity,
            role,
        )

    for pair, group in sorted(pair_groups.items()):
        if len(group) <= 1:
            continue
        roles = [record.get("role") for record in group]
        for record in group:
            if not record.get("role"):
                issues.append(
                    GraphIssue(
                        "error",
                        "GRAPH_ROLE_REQUIRED",
                        f"{RELATIONSHIP_FILE} > {record['name']}",
                        "multiple relationships connect "
                        f"{pair[0]} and {pair[1]}; configure a unique role in "
                        "graph.relationship_roles",
                    )
                )
        present = [role.casefold() for role in roles if isinstance(role, str)]
        if len(present) != len(set(present)):
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_ROLE_DUPLICATE",
                    f"{RELATIONSHIP_FILE} > graph > relationship_roles",
                    f"roles for {pair[0]} and {pair[1]} must be unique",
                )
            )

    for mapping_name, mapping in (
        ("relationship_roles", config.relationship_roles),
        ("relationship_entities", config.relationship_entities),
    ):
        for relationship_name in sorted(set(mapping) - known_names):
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_RELATIONSHIP_CONFIG_ORPHAN",
                    f"{RELATIONSHIP_FILE} > graph > {mapping_name} > {relationship_name}",
                    "configuration references an unknown relationship",
                )
            )

    validate_bridge_policies(records, nodes, config, dialect, issues)
    records.sort(key=lambda item: item["name"])
    return records, node_entities


def _condition_columns(
    condition: str,
    models: tuple[str, str],
    dialect: str | None,
    path: str,
    issues: list[GraphIssue],
) -> dict[str, list[str]]:
    columns: dict[str, list[str]] = {models[0]: [], models[1]: []}
    try:
        parsed = parse_calculation_expression(
            condition,
            dialect=dialect,
            name=f"relationship condition at {path}",
        )
    except GraphPlanningError as exc:
        issues.append(
            GraphIssue(
                "error",
                "GRAPH_RELATIONSHIP_CONDITION_INVALID",
                path,
                f"cannot parse condition: {exc}",
            )
        )
        return columns

    if parsed.find(exp.Or) is not None:
        issues.append(
            GraphIssue(
                "error",
                "GRAPH_RELATIONSHIP_CONDITION_UNSAFE",
                path,
                "OR predicates are not supported in graph relationships",
            )
        )
    equalities = list(parsed.find_all(exp.EQ))
    if not equalities:
        issues.append(
            GraphIssue(
                "error",
                "GRAPH_RELATIONSHIP_KEYS_MISSING",
                path,
                "condition must contain at least one column equality",
            )
        )
        return columns

    expected = {model.casefold(): model for model in models}
    for equality in equalities:
        left = equality.this
        right = equality.expression
        if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_RELATIONSHIP_KEY_INVALID",
                    path,
                    "every equality must compare one column from each declared node",
                )
            )
            continue
        left_model = expected.get(left.table.casefold()) if left.table else None
        right_model = expected.get(right.table.casefold()) if right.table else None
        if left_model is None or right_model is None or left_model == right_model:
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_RELATIONSHIP_QUALIFIER_INVALID",
                    path,
                    "join columns must be qualified by the two declared model names",
                )
            )
            continue
        for model, field in ((left_model, left.name), (right_model, right.name)):
            if field and field not in columns[model]:
                columns[model].append(field)
    return columns


def _validate_condition_fields(
    models: tuple[str, str],
    condition_columns: dict[str, list[str]],
    nodes: dict[str, dict[str, Any]],
    path: str,
    issues: list[GraphIssue],
) -> None:
    """Reject relationship keys that are absent from a declared graph node."""

    for model in models:
        available = {field.casefold() for field in nodes[model]["field_names"]}
        for field in condition_columns.get(model, []):
            if field.casefold() in available:
                continue
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_RELATIONSHIP_FIELD_MISSING",
                    path,
                    f"node '{model}' does not expose relationship field '{field}'",
                )
            )


def _safe_directions(
    models: tuple[str, str], join_type: str
) -> tuple[tuple[str, str], ...]:
    if join_type == "MANY_TO_ONE":
        return ((models[0], models[1]),)
    if join_type == "ONE_TO_MANY":
        return ((models[1], models[0]),)
    if join_type == "ONE_TO_ONE":
        return ((models[0], models[1]), (models[1], models[0]))
    return ()


def _one_side(models: tuple[str, str], join_type: str) -> str | None:
    if join_type == "MANY_TO_ONE":
        return models[1]
    if join_type == "ONE_TO_MANY":
        return models[0]
    return None


def _validate_cardinality(
    *,
    name: str,
    models: tuple[str, str],
    join_type: str,
    bridge_configured: bool,
    condition_columns: dict[str, list[str]],
    nodes: dict[str, dict[str, Any]],
    path: str,
    issues: list[GraphIssue],
) -> str:
    one_sides: tuple[str, ...]
    if join_type == "MANY_TO_ONE":
        one_sides = (models[1],)
    elif join_type == "ONE_TO_MANY":
        one_sides = (models[0],)
    elif join_type == "ONE_TO_ONE":
        one_sides = models
    else:
        if bridge_configured:
            return "bridge_pending"
        issues.append(
            GraphIssue(
                "warning",
                "GRAPH_MANY_TO_MANY_DISABLED",
                path,
                "MANY_TO_MANY is retained for discovery but excluded from governed "
                "queryability until a verified Bridge/Allocation policy is compiled",
            )
        )
        return "disabled"

    verified = True
    for one_side in one_sides:
        primary_keys = set(nodes[one_side]["primary_keys"])
        join_fields = set(condition_columns.get(one_side, []))
        if not primary_keys:
            verified = False
            issues.append(
                GraphIssue(
                    "warning",
                    "GRAPH_CARDINALITY_UNVERIFIED",
                    path,
                    f"cannot verify '{name}': one-side node '{one_side}' has no declared primary key",
                )
            )
            continue
        if not primary_keys.issubset(join_fields):
            verified = False
            issues.append(
                GraphIssue(
                    "error",
                    "GRAPH_CARDINALITY_KEY_MISMATCH",
                    path,
                    f"one-side node '{one_side}' joins on {sorted(join_fields)} but its "
                    f"declared primary key is {sorted(primary_keys)}",
                )
            )
    return "verified" if verified else "unverified"


def _add_relationship_entities(
    result: dict[str, list[dict[str, Any]]],
    models: tuple[str, str],
    join_type: str,
    columns: dict[str, list[str]],
    entity: str,
    role: str | None,
) -> None:
    if join_type == "MANY_TO_ONE":
        types = ("foreign", "primary")
    elif join_type == "ONE_TO_MANY":
        types = ("primary", "foreign")
    elif join_type == "ONE_TO_ONE":
        types = ("unique", "unique")
    else:
        types = ("foreign", "foreign")
    for model, entity_type in zip(models, types, strict=True):
        value = {
            "name": entity,
            "type": entity_type,
            "fields": list(columns.get(model, [])),
            "role": role,
        }
        if value not in result[model]:
            result[model].append(value)

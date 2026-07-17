"""Shared master-model policy for every semantic graph query entry point."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from wren.semantic_graph.model import GraphPlanningError


def master_model(definition: dict[str, Any] | None) -> str | None:
    if not isinstance(definition, dict):
        return None
    value = definition.get("masterModel")
    return value if isinstance(value, str) and value else None


def allowed_bindings(
    definition: dict[str, Any] | None,
    bindings: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return all bindings unless a definition pins an authoritative model."""

    candidates = list(bindings)
    master = master_model(definition)
    if master is None:
        return candidates
    return [binding for binding in candidates if binding.get("model") == master]


def source_equivalent_dimension_binding(
    definition: dict[str, Any] | None,
    bindings: Iterable[dict[str, Any]],
    edges: Iterable[dict[str, Any]],
    *,
    source_model: str,
) -> dict[str, Any] | None:
    """Return a source-local binding when it is the master's relationship key.

    ``master_model`` remains authoritative for descriptive attributes. A fact's
    foreign key is already the canonical identity, however, so joining the
    master merely to project the same key can turn valid historical keys into
    nulls when the latest snapshot no longer contains the row.
    """

    master = master_model(definition)
    if master is None or master == source_model:
        return None
    candidates = list(bindings)
    local = next(
        (item for item in candidates if item.get("model") == source_model),
        None,
    )
    authoritative = next(
        (item for item in candidates if item.get("model") == master),
        None,
    )
    if local is None or authoritative is None:
        return None
    local_fields = {
        item
        for item in local.get("requiredFields") or []
        if isinstance(item, str) and item
    }
    master_fields = {
        item
        for item in authoritative.get("requiredFields") or []
        if isinstance(item, str) and item
    }
    if not local_fields or not master_fields:
        return None
    for edge in edges:
        if [source_model, master] not in (edge.get("safeDirections") or []):
            continue
        columns = edge.get("conditionColumns") or {}
        if local_fields.issubset(set(columns.get(source_model) or [])) and (
            master_fields.issubset(set(columns.get(master) or []))
        ):
            return local
    return None


def enforce_master_model(
    *,
    member_kind: str,
    member_name: str,
    definition: dict[str, Any] | None,
    requested_model: str,
) -> None:
    master = master_model(definition)
    if master is None or requested_model == master:
        return
    raise GraphPlanningError(
        "GRAPH_MASTER_DATA_OVERRIDE_FORBIDDEN",
        f"{member_kind} '{member_name}' must use configured master model '{master}'",
        details={
            "memberKind": member_kind,
            "member": member_name,
            "masterModel": master,
            "requestedModel": requested_model,
        },
    )

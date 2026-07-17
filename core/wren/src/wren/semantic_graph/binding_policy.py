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

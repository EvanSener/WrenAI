"""Shared deterministic helpers for ontology graph modules."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping
from urllib.parse import quote


def node_id(node_type: str, *parts: str) -> str:
    return (
        node_type.lower().replace("_", "-")
        + ":"
        + ":".join(quote(str(part), safe="-._~") for part in parts)
    )


def edge_id(edge_type: str, *parts: str) -> str:
    return (
        "edge:"
        + edge_type.lower().replace("_", "-")
        + ":"
        + ":".join(quote(str(part), safe="-._~") for part in parts)
    )


def nonempty(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item:
            continue
        key = item.casefold()
        if key not in seen:
            result.append(item)
            seen.add(key)
    return result


def clean_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): deepcopy(item) for key, item in value.items() if item is not None}


def as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def member_name(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, Mapping):
        return nonempty(value.get("name"))
    return None


def to_osi_expression(value: Any, dialect: str) -> dict[str, Any]:
    if isinstance(value, Mapping) and isinstance(value.get("dialects"), list):
        return deepcopy(dict(value))
    return {
        "dialects": [
            {
                "dialect": dialect,
                "expression": str(value) if value is not None else "",
            }
        ]
    }


def is_time_type(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    upper = value.upper()
    return any(token in upper for token in ("DATE", "TIME", "TIMESTAMP"))


def diagnostic(level: str, code: str, path: str, message: str) -> dict[str, str]:
    return {"level": level, "code": code, "path": path, "message": message}

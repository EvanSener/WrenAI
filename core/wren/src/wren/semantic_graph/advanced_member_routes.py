"""Shared route accessors for graph members with one or many input paths."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any


def member_routes(member: dict[str, Any]) -> list[dict[str, Any]]:
    """Return normalized routes without changing legacy single-path members."""

    routes = member.get("routes")
    if isinstance(routes, list):
        return routes
    return [
        {
            "model": member.get("bindingModel"),
            "path": member.get("path") or [],
            "hops": len(member.get("path") or []),
        }
    ]


def member_paths(member: dict[str, Any]) -> list[list[dict[str, Any]]]:
    return [route.get("path") or [] for route in member_routes(member)]


def member_steps(member: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for path in member_paths(member):
        yield from path


def route_signature(path: list[dict[str, Any]]) -> tuple[tuple[str, str, str], ...]:
    return tuple((step["relationship"], step["from"], step["to"]) for step in path)

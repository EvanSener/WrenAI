"""Shared helpers for the modular ``wren graph`` command group."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Optional

import typer

ProjectPathOpt = Annotated[
    Optional[str],
    typer.Option(
        "--path",
        "-p",
        help=(
            "Project directory. Auto-detected via WREN_PROJECT_HOME, cwd walk, "
            "or ~/.wren/config.yml."
        ),
    ),
]


def discover_project(path: str | None) -> Path:
    from wren.context import discover_project_path  # noqa: PLC0415

    try:
        return discover_project_path(path)
    except SystemExit as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


def output_path(value: str | None) -> Path | None:
    return Path(value).expanduser() if value else None


def artifact_paths(project_path: Path) -> tuple[Path, Path]:
    target = project_path / "target"
    return target / "semantic_graph.json", target / "queryability_index.json"


def load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    if not path.exists():
        typer.echo(
            f"Error: {label} not found: {path}\n  Hint: run `wren graph build` first.",
            err=True,
        )
        raise typer.Exit(1)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: cannot read {label} at {path}: {exc}", err=True)
        raise typer.Exit(1) from exc
    if not isinstance(value, dict):
        typer.echo(f"Error: {label} at {path} must be a JSON object.", err=True)
        raise typer.Exit(1)
    return value


def load_artifacts(project_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    graph_path, index_path = artifact_paths(project_path)
    return (
        load_json_object(graph_path, label="semantic graph"),
        load_json_object(index_path, label="queryability index"),
    )


def load_optional_ontology(project_path: Path) -> dict[str, Any] | None:
    """Load the ontology sidecar when present; semantic members remain a fallback."""

    path = project_path / "target" / "ontology_graph.json"
    if not path.exists():
        return None
    return load_json_object(path, label="ontology graph")


def parse_members(value: str) -> list[str]:
    return list(
        dict.fromkeys(item.strip() for item in value.split(",") if item.strip())
    )


def validate_output(value: str, *, allowed: set[str]) -> str:
    normalized = value.casefold()
    if normalized not in allowed:
        choices = "|".join(sorted(allowed))
        raise typer.BadParameter(f"expected one of: {choices}")
    return normalized

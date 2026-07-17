"""Read-only GQL/Cypher-style inspection command for graph artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Optional

import typer

from wren.graph_cli_support import ProjectPathOpt, discover_project
from wren.semantic_graph.inspector import GraphInspectionError, inspect_graph


def _parameters(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    path = Path(value).expanduser()
    try:
        raw = path.read_text(encoding="utf-8") if path.exists() else value
        parsed = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(f"invalid JSON parameters: {exc}") from exc
    if not isinstance(parsed, dict):
        raise typer.BadParameter("parameters must be a JSON object")
    return parsed


def inspect_command(
    query: Annotated[
        str,
        typer.Option("--query", "-q", help="Read-only MATCH query."),
    ],
    path: ProjectPathOpt = None,
    artifact: Annotated[
        str,
        typer.Option("--artifact", help="Artifact to inspect: semantic or ontology."),
    ] = "ontology",
    graph_file: Annotated[
        Optional[Path],
        typer.Option("--graph-file", help="Explicit graph JSON file."),
    ] = None,
    parameters: Annotated[
        Optional[str],
        typer.Option("--params", help="JSON object or path to a JSON file."),
    ] = None,
    max_rows: Annotated[
        int,
        typer.Option("--max-rows", min=1, help="Hard result row cap."),
    ] = 10_000,
    output: Annotated[
        str,
        typer.Option("--output", "-o", help="Output: json|rows|explain."),
    ] = "json",
) -> None:
    """Inspect semantic or ontology metadata without executing writes."""

    project_path = discover_project(path)
    artifact_name = artifact.casefold()
    if graph_file is None:
        if artifact_name == "semantic":
            graph_file = project_path / "target" / "semantic_graph.json"
        elif artifact_name == "ontology":
            graph_file = project_path / "target" / "ontology_graph.json"
        else:
            raise typer.BadParameter("artifact must be semantic or ontology")
    try:
        result = inspect_graph(
            graph_file,
            query,
            _parameters(parameters),
            max_rows=max_rows,
        )
    except GraphInspectionError as exc:
        typer.echo(json.dumps(exc.as_dict(), ensure_ascii=False, indent=2), err=True)
        raise typer.Exit(1) from exc
    normalized = output.casefold()
    if normalized == "json":
        payload: Any = result
    elif normalized == "rows":
        payload = result["rows"]
    elif normalized == "explain":
        payload = result["explain"]
    else:
        raise typer.BadParameter("output must be json, rows, or explain")
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))

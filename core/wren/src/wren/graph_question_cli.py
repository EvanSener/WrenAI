"""Natural-language resolver command for the additive graph workflow."""

from __future__ import annotations

import json
from typing import Annotated, Optional

import typer

from wren.graph_cli_support import (
    ProjectPathOpt,
    discover_project,
    load_artifacts,
    load_optional_ontology,
    validate_output,
)
from wren.semantic_graph.question import resolve_graph_question


def resolve_command(
    question: Annotated[
        str,
        typer.Argument(help="Natural-language metric and dimension question."),
    ],
    anchor_model: Annotated[
        Optional[str],
        typer.Option(
            "--anchor-model",
            "--anchor",
            help="Optional source anchor used to resolve otherwise equal candidates.",
        ),
    ] = None,
    max_depth: Annotated[
        Optional[int],
        typer.Option("--max-depth", min=0, help="Maximum simple-path depth."),
    ] = None,
    path: ProjectPathOpt = None,
    output: Annotated[
        str,
        typer.Option("--output", "-o", help="Output format: summary|json."),
    ] = "summary",
) -> None:
    """Resolve a question through Ontology nodes and Semantic Graph bindings."""

    output = validate_output(output, allowed={"summary", "json"})
    project_path = discover_project(path)
    graph, index = load_artifacts(project_path)
    ontology = load_optional_ontology(project_path)
    resolution = resolve_graph_question(
        graph,
        index,
        question,
        ontology_graph=ontology,
        anchor_model=anchor_model,
        max_depth=max_depth,
    )
    if output == "json":
        typer.echo(json.dumps(resolution, indent=2, ensure_ascii=False))
        return

    typer.echo(f"Status: {resolution['status']}")
    typer.echo(
        "Metrics: "
        + ", ".join(item["name"] for item in resolution.get("metrics") or [])
    )
    typer.echo(
        "Dimensions: "
        + ", ".join(item["name"] for item in resolution.get("dimensions") or [])
    )
    if resolution.get("selectedAnchor"):
        typer.echo(f"Selected anchor: {resolution['selectedAnchor']}")
    candidates = resolution.get("candidates") or []
    if candidates:
        typer.echo("Candidates:")
        for candidate in candidates:
            typer.echo(
                f"  {candidate['anchorModel']} ({candidate.get('strategy', '?')})"
            )
    if resolution.get("message"):
        typer.echo(f"Message: {resolution['message']}")

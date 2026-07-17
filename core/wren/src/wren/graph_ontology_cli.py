"""Ontology and Apache Ossie commands under ``wren graph ontology``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Optional

import typer

from wren.graph_cli_support import (
    ProjectPathOpt,
    discover_project,
    load_json_object,
    output_path,
)
from wren.semantic_graph.ontology import (
    OntologyInterchangeError,
    compile_ontology_graph,
    export_ontology_to_osi_file,
    import_osi_ontology,
    load_ontology_graph,
    save_ontology_graph,
)

ontology_app = typer.Typer(
    name="ontology",
    help="Build and exchange the graph ontology sidecar.",
    no_args_is_help=True,
)


def _ontology_path(project_path: Path) -> Path:
    return project_path / "target" / "ontology_graph.json"


def _echo_interchange_error(exc: OntologyInterchangeError) -> None:
    typer.echo(str(exc), err=True)
    raise typer.Exit(1) from exc


@ontology_app.command("build")
def build_ontology(
    path: ProjectPathOpt = None,
    output: Annotated[
        Optional[str],
        typer.Option("--output", "-o", help="Ontology JSON output path."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a machine-readable summary."),
    ] = False,
) -> None:
    """Compile label, synonyms, bindings, entities, grains, and hierarchies."""

    project_path = discover_project(path)
    semantic_graph = load_json_object(
        project_path / "target" / "semantic_graph.json", label="semantic graph"
    )
    try:
        graph = compile_ontology_graph(semantic_graph, project_path)
        saved = save_ontology_graph(graph, project_path, output=output_path(output))
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: ontology build failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    summary = {
        "ontologyGraph": str(saved),
        "nodes": len(graph.get("nodes") or []),
        "edges": len(graph.get("edges") or []),
        "diagnostics": len(graph.get("diagnostics") or []),
        "readOnly": graph.get("readOnly", False),
    }
    typer.echo(
        json.dumps(summary, ensure_ascii=False, indent=2)
        if json_output
        else (
            f"Built ontology graph: {summary['nodes']} nodes, "
            f"{summary['edges']} edges → {saved}"
        )
    )


@ontology_app.command("show")
def show_ontology(
    path: ProjectPathOpt = None,
    output: Annotated[
        str,
        typer.Option("--output", "-o", help="Output format: summary|json."),
    ] = "summary",
) -> None:
    """Show the saved ontology graph."""

    project_path = discover_project(path)
    try:
        graph = load_ontology_graph(_ontology_path(project_path))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"Error: cannot load ontology graph: {exc}", err=True)
        raise typer.Exit(1) from exc
    if output.casefold() == "json":
        typer.echo(json.dumps(graph, ensure_ascii=False, indent=2))
        return
    if output.casefold() != "summary":
        raise typer.BadParameter("expected summary or json")
    typer.echo(f"Kind: {graph.get('kind', '?')}")
    typer.echo(f"Read only: {graph.get('readOnly', False)}")
    typer.echo(f"Nodes: {len(graph.get('nodes') or [])}")
    typer.echo(f"Edges: {len(graph.get('edges') or [])}")
    typer.echo(f"Diagnostics: {len(graph.get('diagnostics') or [])}")


@ontology_app.command("import-osi")
def import_osi(
    source: Annotated[Path, typer.Argument(help="Apache Ossie YAML/JSON file.")],
    path: ProjectPathOpt = None,
    semantic_model: Annotated[
        Optional[str],
        typer.Option("--semantic-model", help="Select one semantic model."),
    ] = None,
    output: Annotated[
        Optional[str],
        typer.Option("--output", "-o", help="Ontology JSON path."),
    ] = None,
) -> None:
    """Import an Ossie document as a read-only ontology sidecar."""

    project_path = discover_project(path)
    try:
        graph = import_osi_ontology(source, semantic_model=semantic_model)
        saved = save_ontology_graph(graph, project_path, output=output_path(output))
    except OntologyInterchangeError as exc:
        _echo_interchange_error(exc)
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: Ossie import failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"Imported read-only ontology: {len(graph.get('nodes') or [])} nodes, "
        f"{len(graph.get('edges') or [])} edges → {saved}"
    )


@ontology_app.command("export-osi")
def export_osi(
    path: ProjectPathOpt = None,
    ontology: Annotated[
        Optional[Path],
        typer.Option("--ontology", help="Ontology JSON; defaults to target sidecar."),
    ] = None,
    output: Annotated[
        Optional[Path],
        typer.Option("--output", "-o", help="Ossie .yml or .json output path."),
    ] = None,
    semantic_model: Annotated[
        Optional[str],
        typer.Option("--semantic-model", help="Exported semantic model name."),
    ] = None,
    dialect: Annotated[
        str,
        typer.Option("--dialect", help="Ossie SQL expression dialect."),
    ] = "ANSI_SQL",
) -> None:
    """Export the core Ossie projection plus lossless WREN extensions."""

    project_path = discover_project(path)
    source = ontology or _ontology_path(project_path)
    destination = output or project_path / "target" / "ontology.osi.yml"
    try:
        graph = load_ontology_graph(source)
        saved, diagnostics = export_ontology_to_osi_file(
            graph,
            destination,
            semantic_model_name=semantic_model,
            dialect=dialect,
        )
    except OntologyInterchangeError as exc:
        _echo_interchange_error(exc)
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: Ossie export failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Exported Ossie document: {saved}")
    typer.echo(f"Diagnostics: {len(diagnostics)}")

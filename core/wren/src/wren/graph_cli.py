"""Typer sub-app for additive semantic model graph commands.

The graph command group deliberately reads and writes only the graph-specific
artifacts under ``target/``.  It does not participate in the existing MDL,
Cube, or query build paths.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
import yaml

from wren.graph_cli_support import (
    ProjectPathOpt,
)
from wren.graph_cli_support import (
    discover_project as _discover_project,
)
from wren.graph_cli_support import (
    load_artifacts as _load_artifacts,
)
from wren.graph_cli_support import (
    load_optional_ontology as _load_optional_ontology,
)
from wren.graph_cli_support import (
    output_path as _output_path,
)
from wren.graph_cli_support import (
    parse_members as _parse_members,
)
from wren.graph_cli_support import (
    validate_output as _validate_output,
)
from wren.semantic_graph import (
    GraphCompilationError,
    GraphPlanningError,
    compile_graph_bundle,
    plan_virtual_cube,
    save_graph_bundle,
)
from wren.semantic_graph.advanced_planner import plan_graph_query
from wren.semantic_graph.ontology import (
    compile_ontology_graph,
    save_ontology_graph,
)
from wren.semantic_graph.question import plan_graph_question

graph_app = typer.Typer(
    name="graph",
    help="Build and inspect the additive semantic model graph.",
    no_args_is_help=True,
)


def _plan(
    project_path: Path,
    *,
    source_model: str | None,
    metrics: str | None,
    dimensions: str,
    request_path: Path | None = None,
    question: str | None = None,
    max_depth: int | None = None,
) -> dict[str, Any]:
    semantic_graph, queryability_index = _load_artifacts(project_path)
    try:
        if question is not None:
            if request_path is not None or metrics or dimensions:
                raise GraphPlanningError(
                    "GRAPH_QUESTION_ARGUMENT_CONFLICT",
                    "--question cannot be combined with --request, --metrics, or --dimensions",
                )
            return plan_graph_question(
                semantic_graph,
                queryability_index,
                question,
                ontology_graph=_load_optional_ontology(project_path),
                anchor_model=source_model,
                max_depth=max_depth,
            )
        if request_path is not None:
            if source_model or metrics or dimensions:
                raise GraphPlanningError(
                    "GRAPH_REQUEST_ARGUMENT_CONFLICT",
                    "--request cannot be combined with --source, --metrics, or --dimensions",
                )
            try:
                request = yaml.safe_load(request_path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as exc:
                raise GraphPlanningError(
                    "GRAPH_REQUEST_FILE_INVALID",
                    f"cannot load graph request '{request_path}': {exc}",
                ) from exc
            if not isinstance(request, dict):
                raise GraphPlanningError(
                    "GRAPH_REQUEST_FILE_INVALID",
                    "graph request root must be an object",
                )
            return plan_graph_query(semantic_graph, queryability_index, request)
        if not source_model or not metrics:
            raise GraphPlanningError(
                "GRAPH_REQUEST_REQUIRED",
                "provide --request or both --source and --metrics",
            )
        return plan_virtual_cube(
            semantic_graph,
            queryability_index,
            source_model=source_model,
            metrics=_parse_members(metrics),
            dimensions=_parse_members(dimensions),
        )
    except GraphPlanningError as exc:
        typer.echo(f"Error [{exc.code}]: {exc}", err=True)
        if exc.details is not None:
            typer.echo(json.dumps(exc.details, indent=2, ensure_ascii=False), err=True)
        raise typer.Exit(1) from exc


@graph_app.command()
def build(
    path: ProjectPathOpt = None,
    max_hops: Annotated[
        Optional[int],
        typer.Option(
            "--max-hops",
            min=0,
            help="Override graph.max_hops for this build (phase one defaults to 2).",
        ),
    ] = None,
    graph_output: Annotated[
        Optional[str],
        typer.Option(
            "--graph-output",
            help="Semantic graph output path. Defaults to target/semantic_graph.json.",
        ),
    ] = None,
    index_output: Annotated[
        Optional[str],
        typer.Option(
            "--index-output",
            help=(
                "Queryability index output path. Defaults to "
                "target/queryability_index.json."
            ),
        ),
    ] = None,
    ontology_output: Annotated[
        Optional[str],
        typer.Option(
            "--ontology-output",
            help="Ontology output path. Defaults to target/ontology_graph.json.",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a machine-readable build summary."),
    ] = False,
) -> None:
    """Compile and save graph-only artifacts from project metadata."""

    project_path = _discover_project(path)
    try:
        bundle = compile_graph_bundle(project_path, max_hops=max_hops)
        graph_path, index_path = save_graph_bundle(
            bundle,
            project_path,
            graph_output=_output_path(graph_output),
            index_output=_output_path(index_output),
        )
        ontology_graph = compile_ontology_graph(bundle.semantic_graph, project_path)
        ontology_path = save_ontology_graph(
            ontology_graph,
            project_path,
            output=_output_path(ontology_output),
        )
    except GraphCompilationError as exc:
        for issue in exc.issues:
            typer.echo(str(issue), err=True)
        typer.echo("\nGraph build aborted due to compilation errors.", err=True)
        raise typer.Exit(1) from exc
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: graph build failed: {exc}", err=True)
        raise typer.Exit(1) from exc

    graph = bundle.semantic_graph
    index = bundle.queryability_index
    summary = {
        "project": str(project_path),
        "nodes": len(graph.get("nodes") or []),
        "edges": len(graph.get("edges") or []),
        "metricBindings": len(graph.get("metricBindings") or []),
        "dimensionBindings": len(graph.get("dimensionBindings") or []),
        "queryableMetricBindings": len(index.get("bindings") or []),
        "warnings": sum(issue.level == "warning" for issue in bundle.issues),
        "semanticGraph": str(graph_path),
        "queryabilityIndex": str(index_path),
        "ontologyGraph": str(ontology_path),
        "ontologyNodes": len(ontology_graph.get("nodes") or []),
        "ontologyEdges": len(ontology_graph.get("edges") or []),
    }
    if json_output:
        typer.echo(json.dumps(summary, indent=2, ensure_ascii=False))
        return

    typer.echo(
        "Built semantic graph: "
        f"{summary['nodes']} nodes, {summary['edges']} edges, "
        f"{summary['metricBindings']} metric bindings, "
        f"{summary['dimensionBindings']} dimension bindings"
    )
    typer.echo(f"Semantic graph: {graph_path}")
    typer.echo(f"Queryability index: {index_path}")
    typer.echo(f"Ontology graph: {ontology_path}")
    if summary["warnings"]:
        typer.echo(f"Warnings: {summary['warnings']} (inspect with `wren graph show`)")


@graph_app.command()
def show(
    path: ProjectPathOpt = None,
    output: Annotated[
        str,
        typer.Option("--output", "-o", help="Output format: summary|json."),
    ] = "summary",
) -> None:
    """Show the saved semantic graph and queryability index."""

    output = _validate_output(output, allowed={"summary", "json"})
    project_path = _discover_project(path)
    graph, index = _load_artifacts(project_path)
    if output == "json":
        typer.echo(
            json.dumps(
                {"semanticGraph": graph, "queryabilityIndex": index},
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    project = graph.get("project") or {}
    config = graph.get("config") or {}
    diagnostics = graph.get("diagnostics") or []
    typer.echo(f"Project: {project.get('name', '?')}")
    typer.echo(f"Data source: {project.get('dataSource', '?')}")
    typer.echo(f"Path: {project_path}")
    typer.echo(f"Edge source: {graph.get('edgeSource', '?')}")
    typer.echo(f"Safe path limit: {index.get('maxHops', config.get('maxHops', '?'))}")
    typer.echo(
        "Graph: "
        f"{len(graph.get('nodes') or [])} nodes, "
        f"{len(graph.get('edges') or [])} edges, "
        f"{len(graph.get('metrics') or [])} metrics, "
        f"{len(graph.get('dimensions') or [])} dimensions"
    )
    typer.echo(
        "Bindings: "
        f"{len(graph.get('metricBindings') or [])} metric, "
        f"{len(graph.get('dimensionBindings') or [])} dimension"
    )
    typer.echo(f"Attribute conflicts: {len(graph.get('attributeConflicts') or [])}")

    bindings = index.get("bindings") or []
    if bindings:
        typer.echo("\nQueryability:")
        for binding in bindings:
            valid = binding.get("validDimensions") or []
            invalid = binding.get("invalidDimensions") or []
            typer.echo(
                f"  {binding.get('metric', '?')} @ "
                f"{binding.get('sourceModel', '?')}: "
                f"{len(valid)} valid, {len(invalid)} rejected dimensions"
            )

    if diagnostics:
        typer.echo("\nDiagnostics:")
        for issue in diagnostics:
            typer.echo(
                f"  [{str(issue.get('level', '?')).upper()}] "
                f"{issue.get('code', '?')} at {issue.get('path', '?')}: "
                f"{issue.get('message', '')}"
            )


@graph_app.command()
def explain(
    source_model: Annotated[
        Optional[str],
        typer.Option(
            "--source-model",
            "--source",
            "-s",
            help="Fact model that binds all requested metrics.",
        ),
    ] = None,
    metrics: Annotated[
        Optional[str],
        typer.Option("--metrics", "-m", help="Comma-separated global metric names."),
    ] = None,
    dimensions: Annotated[
        str,
        typer.Option(
            "--dimensions", "-d", help="Comma-separated global dimension names."
        ),
    ] = "",
    request: Annotated[
        Optional[Path],
        typer.Option(
            "--request",
            help="YAML/JSON dynamic graph request for fanout, multi-fact, or arbitrary-depth planning.",
        ),
    ] = None,
    question: Annotated[
        Optional[str],
        typer.Option(
            "--question",
            "-q",
            help="Resolve a natural-language question through the graph frontend.",
        ),
    ] = None,
    max_depth: Annotated[
        Optional[int],
        typer.Option("--max-depth", min=0, help="Question path search depth."),
    ] = None,
    path: ProjectPathOpt = None,
    output: Annotated[
        str,
        typer.Option("--output", "-o", help="Output format: summary|json."),
    ] = "summary",
) -> None:
    """Explain a governed graph plan, selected paths, fanout, and merge stages."""

    output = _validate_output(output, allowed={"summary", "json"})
    project_path = _discover_project(path)
    plan = _plan(
        project_path,
        source_model=source_model,
        metrics=metrics,
        dimensions=dimensions,
        request_path=request,
        question=question,
        max_depth=max_depth,
    )
    if output == "json":
        typer.echo(json.dumps(plan, indent=2, ensure_ascii=False))
        return

    if plan.get("kind") == "DYNAMIC_VIRTUAL_CUBE":
        typer.echo(f"Plan: {plan.get('kind')}")
        typer.echo(f"Strategy: {plan.get('strategy', '?')}")
        if plan.get("frontendResolution"):
            typer.echo("\nQuestion resolution:")
            typer.echo(
                json.dumps(plan["frontendResolution"], indent=2, ensure_ascii=False)
            )
        typer.echo("\nGraph explain:")
        typer.echo(
            json.dumps(plan.get("graphExplain") or {}, indent=2, ensure_ascii=False)
        )
        typer.echo("\nSQL:")
        typer.echo(plan.get("sql", ""))
        return

    typer.echo(f"Plan: {plan.get('kind', '?')}")
    typer.echo(f"Source: {plan.get('sourceModel', '?')}")
    typer.echo(
        "Metrics: "
        + ", ".join(metric.get("name", "?") for metric in plan.get("metrics") or [])
    )
    planned_dimensions = plan.get("dimensions") or []
    if planned_dimensions:
        typer.echo("Dimensions:")
        for dimension in planned_dimensions:
            master = " [master]" if dimension.get("isMaster") else ""
            typer.echo(
                f"  {dimension.get('name', '?')} <- "
                f"{dimension.get('bindingModel', '?')} "
                f"({dimension.get('hops', 0)} hop(s)){master}"
            )
            for step in dimension.get("path") or []:
                role = f" role={step['role']}" if step.get("role") else ""
                typer.echo(
                    f"    {step.get('from', '?')} -> {step.get('to', '?')} "
                    f"via {step.get('relationship', '?')}{role}"
                )
    else:
        typer.echo("Dimensions: none")

    joins = plan.get("joins") or []
    typer.echo(f"Joins: {len(joins)} (policy: {plan.get('fanoutPolicy', '?')})")
    typer.echo("\nSQL:")
    typer.echo(plan.get("sql", ""))


@graph_app.command()
def query(
    source_model: Annotated[
        Optional[str],
        typer.Option(
            "--source-model",
            "--source",
            "-s",
            help="Fact model that binds all requested metrics.",
        ),
    ] = None,
    metrics: Annotated[
        Optional[str],
        typer.Option("--metrics", "-m", help="Comma-separated global metric names."),
    ] = None,
    dimensions: Annotated[
        str,
        typer.Option(
            "--dimensions", "-d", help="Comma-separated global dimension names."
        ),
    ] = "",
    request: Annotated[
        Optional[Path],
        typer.Option(
            "--request",
            help="YAML/JSON dynamic graph request for fanout, multi-fact, or arbitrary-depth planning.",
        ),
    ] = None,
    question: Annotated[
        Optional[str],
        typer.Option(
            "--question",
            "-q",
            help="Resolve a natural-language question through the graph frontend.",
        ),
    ] = None,
    max_depth: Annotated[
        Optional[int],
        typer.Option("--max-depth", min=0, help="Question path search depth."),
    ] = None,
    path: ProjectPathOpt = None,
    output: Annotated[
        str,
        typer.Option("--output", "-o", help="Output format: sql|json."),
    ] = "sql",
) -> None:
    """Generate SQL for a governed dynamic virtual Cube without executing it."""

    output = _validate_output(output, allowed={"sql", "json"})
    project_path = _discover_project(path)
    plan = _plan(
        project_path,
        source_model=source_model,
        metrics=metrics,
        dimensions=dimensions,
        request_path=request,
        question=question,
        max_depth=max_depth,
    )
    if output == "json":
        typer.echo(json.dumps(plan, indent=2, ensure_ascii=False))
        return
    typer.echo(plan["sql"])


@graph_app.command()
def discover(
    anchor_model: Annotated[
        str,
        typer.Option(
            "--anchor-model", "--anchor", help="Graph node used as discovery anchor."
        ),
    ],
    path: ProjectPathOpt = None,
    max_depth: Annotated[
        Optional[int],
        typer.Option(
            "--max-depth",
            min=0,
            help="Maximum simple-path depth; defaults to all nodes minus one.",
        ),
    ] = None,
) -> None:
    """List all reachable governed members and raw node attributes."""

    project_path = _discover_project(path)
    semantic_graph, queryability_index = _load_artifacts(project_path)
    request: dict[str, Any] = {
        "anchorModel": anchor_model,
        "includeReachable": True,
    }
    if max_depth is not None:
        request["maxDepth"] = max_depth
    try:
        plan = plan_graph_query(semantic_graph, queryability_index, request)
    except GraphPlanningError as exc:
        typer.echo(f"Error [{exc.code}]: {exc}", err=True)
        if exc.details is not None:
            typer.echo(json.dumps(exc.details, indent=2, ensure_ascii=False), err=True)
        raise typer.Exit(1) from exc
    virtual = (plan.get("relationalPlan") or {}).get("virtualWideTable") or {}
    typer.echo(json.dumps(virtual, ensure_ascii=False, indent=2))


from wren.graph_inspect_cli import inspect_command  # noqa: E402
from wren.graph_ontology_cli import ontology_app  # noqa: E402
from wren.graph_question_cli import resolve_command  # noqa: E402

graph_app.command(name="inspect")(inspect_command)
graph_app.command(name="resolve")(resolve_command)
graph_app.add_typer(ontology_app)

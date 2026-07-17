"""Additive semantic model graph compiler and query planner.

This package is intentionally separate from the existing MDL/Cube build path.
Only ``wren graph`` imports it, so projects that never opt into graph commands
retain the existing runtime and manifest behavior.
"""

from wren.semantic_graph.advanced_planner import plan_graph_query
from wren.semantic_graph.compiler import (
    GraphCompilationError,
    compile_graph_bundle,
    save_graph_bundle,
)
from wren.semantic_graph.frontend import GraphQueryFrontend, plan_frontend_query
from wren.semantic_graph.inspection_syntax import GraphInspectionError
from wren.semantic_graph.inspector import inspect_graph
from wren.semantic_graph.ontology import (
    OntologyInterchangeError,
    compile_ontology_graph,
    export_ontology_to_osi,
    export_ontology_to_osi_file,
    import_osi_ontology,
    load_ontology_graph,
    save_ontology_graph,
    save_osi_document,
)
from wren.semantic_graph.planner import GraphPlanningError, plan_virtual_cube
from wren.semantic_graph.question import (
    NaturalLanguageGraphFrontend,
    plan_graph_question,
    resolve_graph_question,
)

__all__ = [
    "GraphCompilationError",
    "GraphInspectionError",
    "GraphPlanningError",
    "GraphQueryFrontend",
    "NaturalLanguageGraphFrontend",
    "OntologyInterchangeError",
    "compile_graph_bundle",
    "compile_ontology_graph",
    "export_ontology_to_osi",
    "export_ontology_to_osi_file",
    "import_osi_ontology",
    "inspect_graph",
    "load_ontology_graph",
    "plan_graph_query",
    "plan_graph_question",
    "plan_frontend_query",
    "plan_virtual_cube",
    "save_ontology_graph",
    "save_osi_document",
    "save_graph_bundle",
    "resolve_graph_question",
]

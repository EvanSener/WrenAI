"""Public facade for ontology graph and Apache Ossie interoperability.

The feature remains independent from MDL compilation.  Implementation is split
by responsibility while this module preserves the original import surface.
"""

from wren.semantic_graph.ontology_compile import (
    compile_ontology_graph,
    load_ontology_graph,
    save_ontology_graph,
)
from wren.semantic_graph.ontology_osi_export import (
    export_ontology_to_osi,
    export_ontology_to_osi_file,
    save_osi_document,
)
from wren.semantic_graph.ontology_osi_import import import_osi_ontology
from wren.semantic_graph.ontology_types import (
    EDGE_TYPES,
    NODE_TYPES,
    ONTOLOGY_KIND,
    ONTOLOGY_SCHEMA_VERSION,
    OSSIE_VERSION,
    WREN_VENDOR_NAME,
    OntologyInterchangeError,
)

__all__ = [
    "EDGE_TYPES",
    "NODE_TYPES",
    "ONTOLOGY_KIND",
    "ONTOLOGY_SCHEMA_VERSION",
    "OSSIE_VERSION",
    "WREN_VENDOR_NAME",
    "OntologyInterchangeError",
    "compile_ontology_graph",
    "export_ontology_to_osi",
    "export_ontology_to_osi_file",
    "import_osi_ontology",
    "load_ontology_graph",
    "save_ontology_graph",
    "save_osi_document",
]

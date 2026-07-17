"""Import Apache Ossie YAML/JSON as a read-only ontology graph."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from wren.osi import select_semantic_model
from wren.semantic_graph.ontology_osi_common import (
    osi_object_extensions,
    osi_semantics,
    read_osi_source,
)
from wren.semantic_graph.ontology_types import (
    GraphBuilder,
    OntologyInterchangeError,
)
from wren.semantic_graph.ontology_utils import edge_id, node_id, nonempty


def import_osi_ontology(
    source: Path | str | Mapping[str, Any],
    *,
    semantic_model: str | None = None,
) -> dict[str, Any]:
    """Import Apache Ossie YAML/JSON as a read-only ontology graph.

    Parsing and semantic-model selection reuse :mod:`wren.osi`.  The complete
    input document is retained in ``extensions.osi.sourceDocument`` so unknown
    future fields and all vendor extensions survive the import.
    """

    document = read_osi_source(source)
    selected, selection_issues = select_semantic_model(document, semantic_model)
    diagnostics = [
        {
            "level": issue.level,
            "code": "OSSIE_IMPORT_SELECTION_ERROR"
            if issue.level == "error"
            else "OSSIE_IMPORT_SELECTION_WARNING",
            "path": issue.path,
            "message": issue.message,
        }
        for issue in selection_issues
    ]
    if not selected:
        raise OntologyInterchangeError(diagnostics)

    builder = GraphBuilder()
    builder.diagnostics.extend(diagnostics)
    model_name = nonempty(selected.get("name")) or "ossie_semantic_model"
    root_id = node_id("SEMANTIC_MODEL", model_name)
    semantics = osi_semantics(selected)
    builder.add_node(
        node_id=root_id,
        node_type="SEMANTIC_MODEL",
        name=model_name,
        label=selected.get("label"),
        description=semantics["description"],
        synonyms=semantics["synonyms"],
        properties={
            "aiInstructions": semantics["instructions"],
            "aiExamples": semantics["examples"],
            "ossieVersion": document.get("version"),
        },
        extensions=osi_object_extensions(
            selected,
            {
                "name",
                "description",
                "ai_context",
                "datasets",
                "relationships",
                "metrics",
                "custom_extensions",
            },
        ),
    )

    datasets = selected.get("datasets")
    for index, dataset in enumerate(datasets if isinstance(datasets, list) else []):
        if not isinstance(dataset, Mapping):
            builder.issue(
                "warning",
                "OSSIE_IMPORT_DATASET_INVALID",
                f"semantic_model.datasets[{index}]",
                "non-object dataset was preserved only in the sourceDocument extension",
            )
            continue
        _import_osi_dataset(builder, root_id, dataset, index)

    metrics = selected.get("metrics")
    for index, metric in enumerate(metrics if isinstance(metrics, list) else []):
        if not isinstance(metric, Mapping):
            builder.issue(
                "warning",
                "OSSIE_IMPORT_METRIC_INVALID",
                f"semantic_model.metrics[{index}]",
                "non-object metric was preserved only in the sourceDocument extension",
            )
            continue
        _import_osi_metric(builder, root_id, metric, index)

    relationships = selected.get("relationships")
    for index, relationship in enumerate(
        relationships if isinstance(relationships, list) else []
    ):
        if not isinstance(relationship, Mapping):
            builder.issue(
                "warning",
                "OSSIE_IMPORT_RELATIONSHIP_INVALID",
                f"semantic_model.relationships[{index}]",
                "non-object relationship was preserved only in the sourceDocument extension",
            )
            continue
        _import_osi_relationship(builder, relationship, index)

    all_models = document.get("semantic_model")
    if isinstance(all_models, list) and len(all_models) > 1:
        builder.issue(
            "info",
            "OSSIE_IMPORT_MODEL_SELECTED",
            "semantic_model",
            f"imported '{model_name}' from {len(all_models)} semantic models; the complete document is preserved in extensions",
        )

    return builder.artifact(
        source={
            "format": "apache-ossie",
            "version": document.get("version"),
            "semanticModel": model_name,
        },
        read_only=True,
        extensions={"osi": {"sourceDocument": deepcopy(document)}},
    )


def _import_osi_dataset(
    builder: GraphBuilder,
    root_id: str,
    dataset: Mapping[str, Any],
    index: int,
) -> None:
    name = nonempty(dataset.get("name"))
    if name is None:
        builder.issue(
            "warning",
            "OSSIE_IMPORT_DATASET_NAME_MISSING",
            f"semantic_model.datasets[{index}]",
            "dataset was preserved only in the sourceDocument extension",
        )
        return
    semantics = osi_semantics(dataset)
    dataset_id = node_id("DATASET", name)
    builder.add_node(
        node_id=dataset_id,
        node_type="DATASET",
        name=name,
        label=dataset.get("label"),
        description=semantics["description"],
        synonyms=semantics["synonyms"],
        properties={
            "source": dataset.get("source"),
            "primaryKey": dataset.get("primary_key"),
            "uniqueKeys": dataset.get("unique_keys"),
            "aiInstructions": semantics["instructions"],
            "aiExamples": semantics["examples"],
        },
        extensions=osi_object_extensions(
            dataset,
            {
                "name",
                "source",
                "primary_key",
                "unique_keys",
                "description",
                "ai_context",
                "fields",
                "custom_extensions",
            },
        ),
    )
    builder.add_edge(
        edge_id=edge_id("HAS_DATASET", root_id, dataset_id),
        edge_type="HAS_DATASET",
        source_id=root_id,
        target_id=dataset_id,
    )
    fields = dataset.get("fields")
    for field_index, field in enumerate(fields if isinstance(fields, list) else []):
        if not isinstance(field, Mapping) or not nonempty(field.get("name")):
            builder.issue(
                "warning",
                "OSSIE_IMPORT_FIELD_INVALID",
                f"semantic_model.datasets[{index}].fields[{field_index}]",
                "field was preserved only in the sourceDocument extension",
            )
            continue
        field_name = str(field["name"])
        dimension = field.get("dimension")
        node_type = "DIMENSION" if isinstance(dimension, Mapping) else "FIELD"
        field_id = node_id(node_type, name, field_name)
        field_semantics = osi_semantics(field)
        builder.add_node(
            node_id=field_id,
            node_type=node_type,
            name=field_name,
            label=field.get("label"),
            description=field_semantics["description"],
            synonyms=field_semantics["synonyms"],
            properties={
                "dataset": name,
                "expression": field.get("expression"),
                "isTime": dimension.get("is_time")
                if isinstance(dimension, Mapping)
                else False,
                "aiInstructions": field_semantics["instructions"],
                "aiExamples": field_semantics["examples"],
                "ordinal": field_index,
            },
            extensions=osi_object_extensions(
                field,
                {
                    "name",
                    "expression",
                    "dimension",
                    "label",
                    "description",
                    "ai_context",
                    "custom_extensions",
                },
            ),
        )
        builder.add_edge(
            edge_id=edge_id("HAS_FIELD", dataset_id, field_id),
            edge_type="HAS_FIELD",
            source_id=dataset_id,
            target_id=field_id,
            properties={"ordinal": field_index},
        )


def _import_osi_metric(
    builder: GraphBuilder,
    root_id: str,
    metric: Mapping[str, Any],
    index: int,
) -> None:
    name = nonempty(metric.get("name"))
    if name is None:
        builder.issue(
            "warning",
            "OSSIE_IMPORT_METRIC_NAME_MISSING",
            f"semantic_model.metrics[{index}]",
            "metric was preserved only in the sourceDocument extension",
        )
        return
    semantics = osi_semantics(metric)
    metric_id = node_id("METRIC", name)
    builder.add_node(
        node_id=metric_id,
        node_type="METRIC",
        name=name,
        label=metric.get("label"),
        description=semantics["description"],
        synonyms=semantics["synonyms"],
        properties={
            "expression": metric.get("expression"),
            "aiInstructions": semantics["instructions"],
            "aiExamples": semantics["examples"],
        },
        extensions=osi_object_extensions(
            metric,
            {"name", "expression", "description", "ai_context", "custom_extensions"},
        ),
    )
    builder.add_edge(
        edge_id=edge_id("HAS_METRIC", root_id, metric_id),
        edge_type="HAS_METRIC",
        source_id=root_id,
        target_id=metric_id,
    )


def _import_osi_relationship(
    builder: GraphBuilder, relationship: Mapping[str, Any], index: int
) -> None:
    name = nonempty(relationship.get("name")) or f"relationship_{index}"
    source = nonempty(relationship.get("from"))
    target = nonempty(relationship.get("to"))
    if source is None or target is None:
        builder.issue(
            "warning",
            "OSSIE_IMPORT_RELATIONSHIP_ENDPOINT_MISSING",
            f"semantic_model.relationships[{index}]",
            "relationship was preserved only in the sourceDocument extension",
        )
        return
    source_id = builder.ensure_node(
        "DATASET", source, path=f"semantic_model.relationships[{index}]"
    )
    target_id = builder.ensure_node(
        "DATASET", target, path=f"semantic_model.relationships[{index}]"
    )
    semantics = osi_semantics(relationship)
    builder.add_edge(
        edge_id=edge_id("RELATIONSHIP", name),
        edge_type="RELATIONSHIP",
        source_id=source_id,
        target_id=target_id,
        name=name,
        properties={
            "cardinality": "MANY_TO_ONE",
            "fromColumns": relationship.get("from_columns"),
            "toColumns": relationship.get("to_columns"),
            "description": semantics["description"],
            "synonyms": semantics["synonyms"],
            "aiInstructions": semantics["instructions"],
            "aiExamples": semantics["examples"],
        },
        extensions=osi_object_extensions(
            relationship,
            {
                "name",
                "from",
                "to",
                "from_columns",
                "to_columns",
                "ai_context",
                "custom_extensions",
            },
        ),
    )

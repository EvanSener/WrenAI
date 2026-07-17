"""Compile native Wren Cubes and hierarchies into ontology graph objects."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from wren.semantic_graph.ontology_types import GraphBuilder
from wren.semantic_graph.ontology_utils import (
    as_list,
    edge_id,
    member_name,
    node_id,
    nonempty,
)


def compile_wren_cubes(
    builder: GraphBuilder,
    root_id: str,
    cubes: Sequence[Mapping[str, Any]],
) -> None:
    for index, item in enumerate(cubes):
        if not isinstance(item, Mapping) or not nonempty(item.get("name")):
            builder.issue(
                "warning",
                "ONTOLOGY_CUBE_INVALID",
                f"cubes[{index}]",
                "Cube without a stable name was skipped",
            )
            continue
        name = str(item["name"])
        cube_id = node_id("CUBE", name)
        raw_metadata = {
            key: deepcopy(value)
            for key, value in item.items()
            if not str(key).startswith("_")
        }
        builder.add_node(
            node_id=cube_id,
            node_type="CUBE",
            name=name,
            label=item.get("label"),
            description=item.get("description"),
            synonyms=item.get("synonyms"),
            properties={
                "baseObject": item.get("base_object") or item.get("baseObject"),
                "priority": item.get("priority"),
            },
            extensions={"wren": {"metadata": raw_metadata}},
        )
        builder.add_edge(
            edge_id=edge_id("HAS_CUBE", root_id, cube_id),
            edge_type="HAS_CUBE",
            source_id=root_id,
            target_id=cube_id,
        )
        base_object = nonempty(item.get("base_object") or item.get("baseObject"))
        if base_object:
            dataset_id = builder.ensure_node(
                "DATASET", base_object, path=f"cubes/{name} > base_object"
            )
            builder.add_edge(
                edge_id=edge_id("CUBE_BASE_DATASET", cube_id, dataset_id),
                edge_type="CUBE_BASE_DATASET",
                source_id=cube_id,
                target_id=dataset_id,
            )

        for member_index, raw_member in enumerate(as_list(item.get("measures"))):
            member = member_name(raw_member)
            if member is None:
                continue
            metric_id = builder.ensure_node(
                "METRIC", member, path=f"cubes/{name} > measures[{member_index}]"
            )
            builder.add_edge(
                edge_id=edge_id("CUBE_METRIC", cube_id, metric_id),
                edge_type="CUBE_METRIC",
                source_id=cube_id,
                target_id=metric_id,
                properties={"ordinal": member_index},
            )

        for key, edge_type in (
            ("dimensions", "CUBE_DIMENSION"),
            ("time_dimensions", "CUBE_TIME_DIMENSION"),
            ("timeDimensions", "CUBE_TIME_DIMENSION"),
        ):
            if key not in item:
                continue
            for member_index, raw_member in enumerate(as_list(item.get(key))):
                member = member_name(raw_member)
                if member is None:
                    continue
                dimension_id = builder.ensure_node(
                    "DIMENSION", member, path=f"cubes/{name} > {key}[{member_index}]"
                )
                builder.add_edge(
                    edge_id=edge_id(edge_type, cube_id, dimension_id),
                    edge_type=edge_type,
                    source_id=cube_id,
                    target_id=dimension_id,
                    properties={"ordinal": member_index},
                )

        hierarchies = item.get("hierarchies")
        for hierarchy_name, hierarchy_value in (
            sorted(hierarchies.items()) if isinstance(hierarchies, Mapping) else []
        ):
            if not isinstance(hierarchy_name, str) or not hierarchy_name:
                continue
            hierarchy_semantics = (
                hierarchy_value if isinstance(hierarchy_value, Mapping) else {}
            )
            levels = (
                hierarchy_semantics.get("levels")
                if hierarchy_semantics
                else hierarchy_value
            )
            if not isinstance(levels, list):
                builder.issue(
                    "warning",
                    "ONTOLOGY_HIERARCHY_LEVELS_INVALID",
                    f"cubes/{name} > hierarchies.{hierarchy_name}",
                    "hierarchy levels must be a list",
                )
                continue
            hierarchy_id = node_id("HIERARCHY", name, hierarchy_name)
            builder.add_node(
                node_id=hierarchy_id,
                node_type="HIERARCHY",
                name=hierarchy_name,
                label=hierarchy_semantics.get("label"),
                description=hierarchy_semantics.get("description"),
                synonyms=hierarchy_semantics.get("synonyms"),
                properties={"cube": name, "levels": list(levels)},
            )
            builder.add_edge(
                edge_id=edge_id("CUBE_HIERARCHY", cube_id, hierarchy_id),
                edge_type="CUBE_HIERARCHY",
                source_id=cube_id,
                target_id=hierarchy_id,
            )
            for ordinal, raw_level in enumerate(levels):
                level = member_name(raw_level)
                if level is None:
                    builder.issue(
                        "warning",
                        "ONTOLOGY_HIERARCHY_LEVEL_INVALID",
                        f"cubes/{name} > hierarchies.{hierarchy_name}[{ordinal}]",
                        "hierarchy level must reference a named dimension",
                    )
                    continue
                dimension_id = builder.ensure_node(
                    "DIMENSION",
                    level,
                    path=f"cubes/{name} > hierarchies.{hierarchy_name}[{ordinal}]",
                )
                builder.add_edge(
                    edge_id=edge_id(
                        "HIERARCHY_LEVEL", hierarchy_id, str(ordinal), dimension_id
                    ),
                    edge_type="HIERARCHY_LEVEL",
                    source_id=hierarchy_id,
                    target_id=dimension_id,
                    properties={"ordinal": ordinal},
                )

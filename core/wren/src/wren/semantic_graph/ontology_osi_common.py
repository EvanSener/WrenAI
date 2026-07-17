"""Shared Apache Ossie parsing and extension helpers."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from wren.osi import load_osi_file, parse_osi
from wren.semantic_graph.ontology_types import WREN_VENDOR_NAME
from wren.semantic_graph.ontology_utils import nonempty, string_list


def read_osi_source(source: Path | str | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(source, Mapping):
        return deepcopy(dict(source))
    if isinstance(source, Path):
        return load_osi_file(source)
    if not isinstance(source, str):
        raise TypeError("source must be a path, YAML/JSON text, or mapping")
    stripped = source.lstrip()
    if "\n" in source or stripped.startswith(("{", "version:", "semantic_model:")):
        suffix = ".json" if stripped.startswith("{") else ".yaml"
        return parse_osi(source, suffix=suffix)
    return load_osi_file(Path(source))


def osi_semantics(obj: Mapping[str, Any]) -> dict[str, Any]:
    ai_context = obj.get("ai_context")
    synonyms: list[str] = []
    instructions: str | None = None
    examples: list[str] = []
    if isinstance(ai_context, str):
        instructions = ai_context
    elif isinstance(ai_context, Mapping):
        synonyms = string_list(ai_context.get("synonyms"))
        instructions = nonempty(ai_context.get("instructions"))
        examples = string_list(ai_context.get("examples"))
    return {
        "description": nonempty(obj.get("description")),
        "synonyms": synonyms,
        "instructions": instructions,
        "examples": examples,
    }


def osi_object_extensions(
    obj: Mapping[str, Any], known_keys: set[str]
) -> dict[str, Any]:
    osi: dict[str, Any] = {}
    custom = obj.get("custom_extensions")
    if isinstance(custom, list) and custom:
        osi["customExtensions"] = deepcopy(custom)
    unmapped = {
        key: deepcopy(value) for key, value in obj.items() if key not in known_keys
    }
    if unmapped:
        osi["unmapped"] = unmapped
    return {"osi": osi} if osi else {}


def relation_source(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    if value.get("type") == "sql":
        return nonempty(value.get("sql"))
    reference = value.get("tableReference")
    if not isinstance(reference, Mapping):
        return None
    parts = [
        reference.get("catalog"),
        reference.get("schema"),
        reference.get("table"),
    ]
    present = [str(part) for part in parts if isinstance(part, str) and part]
    return ".".join(present) if present else None


def apply_osi_semantics(
    target: dict[str, Any], node: Mapping[str, Any], *, include_label: bool
) -> None:
    description = nonempty(node.get("description"))
    if description:
        target["description"] = description
    if include_label:
        label = nonempty(node.get("label"))
        if label and label != node.get("name"):
            target["label"] = label
    properties = node.get("properties")
    properties = properties if isinstance(properties, Mapping) else {}
    ai_context = make_ai_context(
        synonyms=node.get("synonyms"),
        instructions=properties.get("aiInstructions"),
        examples=properties.get("aiExamples"),
    )
    if ai_context:
        target["ai_context"] = ai_context


def make_ai_context(
    *, synonyms: Any = None, instructions: Any = None, examples: Any = None
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    synonym_list = string_list(synonyms)
    example_list = string_list(examples)
    if synonym_list:
        result["synonyms"] = synonym_list
    if isinstance(instructions, str) and instructions:
        result["instructions"] = instructions
    if example_list:
        result["examples"] = example_list
    return result


def foreign_custom_extensions(
    obj: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(obj, Mapping):
        return []
    extensions = obj.get("extensions")
    if not isinstance(extensions, Mapping):
        return []
    osi = extensions.get("osi")
    if not isinstance(osi, Mapping):
        return []
    custom = osi.get("customExtensions")
    if not isinstance(custom, list):
        return []
    return [
        deepcopy(dict(item))
        for item in custom
        if isinstance(item, Mapping) and item.get("vendor_name") != WREN_VENDOR_NAME
    ]


def node_non_core_extension(
    node: Mapping[str, Any], *, exclude: set[str]
) -> dict[str, Any]:
    properties = node.get("properties")
    properties = properties if isinstance(properties, Mapping) else {}
    payload: dict[str, Any] = {
        "ontology_id": node.get("id"),
        "ontology_type": node.get("type"),
    }
    label = nonempty(node.get("label"))
    if label and label != node.get("name"):
        payload["label"] = label
    non_core = {
        key: deepcopy(value)
        for key, value in properties.items()
        if key not in exclude and key not in {"aiInstructions", "aiExamples"}
    }
    if non_core:
        payload["properties"] = non_core
    return payload


def wren_extension(payload: Mapping[str, Any]) -> dict[str, str]:
    return {
        "vendor_name": WREN_VENDOR_NAME,
        "data": json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ),
    }

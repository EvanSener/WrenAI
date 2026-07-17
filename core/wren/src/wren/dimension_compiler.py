"""Compile project-level semantic dimensions into runtime Cube members.

Dimensions are reusable semantic fields, not copies of physical table columns.
Their expressions may be direct mappings or derived SQL such as ``CASE``
classifications. Cubes only reference the dimensions they expose. During build,
every expression is parsed and its atomic fields are checked against the Cube's
``base_object`` before the source reference is expanded into runtime MDL.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlglot
import yaml
from sqlglot import exp
from sqlglot.errors import ParseError

from wren.metric_compiler import (
    MetricCompilerIssue,
    ObjectFieldResolver,
    dialect_for,
)

_DIMENSION_RUNTIME_FIELDS = frozenset(
    {
        "name",
        "expression",
        "type",
        "label",
        "description",
        "synonyms",
    }
)
_GLOBAL_DIMENSION_FIELDS = _DIMENSION_RUNTIME_FIELDS | {"master_model"}
_REQUIRED_DIMENSION_FIELDS = ("name", "expression", "type")
_CUBE_DIMENSION_KEYS = ("dimensions", "time_dimensions")


class DimensionCompilationError(ValueError):
    """Raised when a Cube cannot safely bind its dimension references."""

    def __init__(self, issues: list[MetricCompilerIssue]):
        self.issues = tuple(issues)
        details = "\n".join(f"- {issue.path}: {issue.message}" for issue in issues)
        super().__init__(f"Dimension compilation failed:\n{details}")


@dataclass(frozen=True)
class _DimensionDefinition:
    data: dict[str, Any]
    path: str


def load_dimensions(project_path: Path) -> list[dict]:
    """Load ``dimensions/<name>/metadata.yml`` in deterministic order."""

    dimensions_dir = project_path / "dimensions"
    if not dimensions_dir.is_dir():
        return []

    dimensions: list[dict] = []
    for directory in sorted(dimensions_dir.iterdir()):
        if not directory.is_dir():
            continue
        metadata_file = directory / "metadata.yml"
        if not metadata_file.exists():
            continue
        dimension = yaml.safe_load(metadata_file.read_text(encoding="utf-8")) or {}
        if not isinstance(dimension, dict):
            dimension = {"_invalid_value": dimension}
        dimension["_source_file"] = str(metadata_file.relative_to(dimensions_dir))
        dimensions.append(dimension)
    return dimensions


def compile_cube_dimensions(
    *,
    cubes: list[dict],
    dimensions: list[dict],
    models: list[dict],
    views: list[dict],
    data_source: str | None,
) -> list[dict]:
    """Expand global dimension references and reject invalid bindings."""

    compiled, issues = analyze_cube_dimensions(
        cubes=cubes,
        dimensions=dimensions,
        models=models,
        views=views,
        data_source=data_source,
    )
    if issues:
        raise DimensionCompilationError(issues)
    return compiled


def analyze_cube_dimensions(
    *,
    cubes: list[dict],
    dimensions: list[dict],
    models: list[dict],
    views: list[dict],
    data_source: str | None,
) -> tuple[list[dict], list[MetricCompilerIssue]]:
    """Return expanded Cubes plus all global-dimension binding issues."""

    issues: list[MetricCompilerIssue] = []
    global_definitions = _global_dimension_registry(dimensions, issues)
    dialect = dialect_for(data_source)
    parsed_global: dict[str, tuple[str, ...]] = {}
    for name, definition in global_definitions.items():
        parsed_global[name] = _atomic_fields(definition, dialect, issues)

    issues.extend(_repeated_inline_dimension_issues(cubes))
    object_fields = ObjectFieldResolver(models, views, dialect)
    compiled_cubes: list[dict] = []

    for cube_index, original_cube in enumerate(cubes):
        cube = deepcopy(original_cube)
        source = cube.get("_source_file", f"cubes[{cube_index}]")
        cube_path = f"cubes/{source}"
        cube_name = cube.get("name")
        if not isinstance(cube_name, str) or not cube_name:
            compiled_cubes.append(cube)
            continue

        definitions = dict(global_definitions)
        canonical_names = {name.casefold(): name for name in definitions}
        selected_by_key: dict[str, list[str]] = {}
        selected_roles: dict[str, str] = {}

        for member_key in _CUBE_DIMENSION_KEYS:
            raw_members = cube.get(member_key) or []
            member_path = f"{cube_path} > {cube_name} > {member_key}"
            if not isinstance(raw_members, list):
                issues.append(
                    MetricCompilerIssue(
                        member_path,
                        f"CUBE_DIMENSIONS_INVALID: {member_key} must be a list",
                    )
                )
                selected_by_key[member_key] = []
                continue

            selected: list[str] = []
            for member_index, raw_member in enumerate(raw_members):
                path = f"{member_path}[{member_index}]"
                name: str | None = None
                if isinstance(raw_member, str):
                    reference = raw_member.strip()
                    name = canonical_names.get(reference.casefold())
                    if not reference or name is None:
                        issues.append(
                            MetricCompilerIssue(
                                path,
                                "CUBE_DIMENSION_NOT_FOUND: global dimension "
                                f"'{raw_member}' is not defined under dimensions/",
                            )
                        )
                        continue
                elif isinstance(raw_member, dict):
                    inline = deepcopy(raw_member)
                    _validate_dimension_shape(inline, path, issues)
                    inline_name = inline.get("name")
                    if not isinstance(inline_name, str) or not inline_name:
                        continue
                    folded = inline_name.casefold()
                    if folded in canonical_names:
                        issues.append(
                            MetricCompilerIssue(
                                path,
                                "CUBE_DIMENSION_SHADOWED: inline dimension "
                                f"'{inline_name}' conflicts with global dimension "
                                f"'{canonical_names[folded]}'",
                            )
                        )
                        continue
                    canonical_names[folded] = inline_name
                    definitions[inline_name] = _DimensionDefinition(inline, path)
                    name = inline_name
                else:
                    issues.append(
                        MetricCompilerIssue(
                            path,
                            "CUBE_DIMENSION_INVALID: member must be a global "
                            "dimension name or an inline dimension object",
                        )
                    )
                    continue

                previous_role = selected_roles.get(name.casefold())
                if previous_role is not None and previous_role != member_key:
                    issues.append(
                        MetricCompilerIssue(
                            path,
                            "CUBE_DIMENSION_ROLE_CONFLICT: dimension "
                            f"'{name}' cannot be both dimensions and time_dimensions",
                        )
                    )
                    continue
                selected_roles[name.casefold()] = member_key
                selected.append(name)

            for duplicate in _duplicates(selected):
                issues.append(
                    MetricCompilerIssue(
                        member_path,
                        f"CUBE_DIMENSION_DUPLICATE: dimension '{duplicate}' is referenced more than once",
                    )
                )
            selected_by_key[member_key] = selected

        parsed_local: dict[str, tuple[str, ...]] = {}
        required_fields: dict[str, tuple[str, str]] = {}
        for member_key, selected in selected_by_key.items():
            for name in selected:
                definition = definitions[name]
                fields = parsed_global.get(name)
                if fields is None:
                    fields = parsed_local.get(name)
                if fields is None:
                    fields = _atomic_fields(definition, dialect, issues)
                    parsed_local[name] = fields
                for field_name in fields:
                    required_fields.setdefault(field_name, (member_key, name))

        base_object = cube.get("base_object")
        if required_fields and isinstance(base_object, str) and base_object:
            available, field_issues = object_fields.fields_for(base_object, cube_path)
            issues.extend(field_issues)
            if available is not None:
                available_folded = {field.casefold() for field in available}
                for field_name, (member_key, name) in sorted(required_fields.items()):
                    if field_name.casefold() in available_folded:
                        continue
                    issues.append(
                        MetricCompilerIssue(
                            f"{cube_path} > {cube_name} > {member_key} '{name}'",
                            "CUBE_DIMENSION_FIELD_MISSING: "
                            f"base_object '{base_object}' does not expose atomic "
                            f"field '{field_name}' required by dimension '{name}'",
                        )
                    )

        for member_key, selected in selected_by_key.items():
            cube[member_key] = [
                _public_dimension(definitions[name].data)
                for name in selected
                if name in definitions
            ]
        compiled_cubes.append(cube)

    return compiled_cubes, _deduplicate_issues(issues)


def _global_dimension_registry(
    dimensions: list[dict], issues: list[MetricCompilerIssue]
) -> dict[str, _DimensionDefinition]:
    definitions: dict[str, _DimensionDefinition] = {}
    canonical_names: dict[str, str] = {}
    for index, dimension in enumerate(dimensions):
        source = dimension.get("_source_file", f"dimensions[{index}]")
        path = f"dimensions/{source}"
        _validate_dimension_shape(dimension, path, issues, allow_master_model=True)
        name = dimension.get("name")
        if not isinstance(name, str) or not name:
            continue

        directory_name = Path(str(source)).parts[0]
        if directory_name != name:
            issues.append(
                MetricCompilerIssue(
                    path,
                    "DIMENSION_DIRECTORY_MISMATCH: directory name "
                    f"'{directory_name}' must match dimension name '{name}'",
                )
            )

        folded = name.casefold()
        if folded in canonical_names:
            issues.append(
                MetricCompilerIssue(
                    path,
                    f"DIMENSION_DUPLICATE: dimension name '{name}' is already defined",
                )
            )
            continue
        canonical_names[folded] = name
        definitions[name] = _DimensionDefinition(_global_dimension(dimension), path)
    return definitions


def _validate_dimension_shape(
    dimension: dict[str, Any],
    path: str,
    issues: list[MetricCompilerIssue],
    *,
    allow_master_model: bool = False,
) -> None:
    if "_invalid_value" in dimension:
        issues.append(
            MetricCompilerIssue(
                path, "DIMENSION_INVALID: metadata.yml must contain a YAML object"
            )
        )
        return

    for field_name in _REQUIRED_DIMENSION_FIELDS:
        value = dimension.get(field_name)
        if not isinstance(value, str) or not value.strip():
            issues.append(
                MetricCompilerIssue(
                    path,
                    f"DIMENSION_FIELD_REQUIRED: '{field_name}' must be a non-empty string",
                )
            )

    allowed_fields = (
        _GLOBAL_DIMENSION_FIELDS if allow_master_model else _DIMENSION_RUNTIME_FIELDS
    )
    unknown = sorted(
        key
        for key in dimension
        if not key.startswith("_") and key not in allowed_fields
    )
    if unknown:
        issues.append(
            MetricCompilerIssue(
                path,
                "DIMENSION_FIELD_UNKNOWN: unsupported field(s): " + ", ".join(unknown),
            )
        )

    if allow_master_model and "master_model" in dimension:
        master_model = dimension.get("master_model")
        if not isinstance(master_model, str) or not master_model.strip():
            issues.append(
                MetricCompilerIssue(
                    path,
                    "DIMENSION_MASTER_MODEL_INVALID: master_model must be a non-empty string",
                )
            )

    synonyms = dimension.get("synonyms")
    if synonyms is not None:
        valid = (
            isinstance(synonyms, list)
            and all(isinstance(item, str) and item.strip() for item in synonyms)
            and len({item.casefold() for item in synonyms}) == len(synonyms)
        )
        if not valid:
            issues.append(
                MetricCompilerIssue(
                    path,
                    "DIMENSION_SYNONYMS_INVALID: synonyms must be a unique list "
                    "of non-empty strings",
                )
            )


def _atomic_fields(
    definition: _DimensionDefinition,
    dialect: str | None,
    issues: list[MetricCompilerIssue],
) -> tuple[str, ...]:
    expression = definition.data.get("expression")
    if not isinstance(expression, str) or not expression.strip():
        return ()
    try:
        parsed = sqlglot.parse_one(expression, dialect=dialect)
    except (ParseError, ValueError) as exc:
        issues.append(
            MetricCompilerIssue(
                definition.path,
                f"DIMENSION_EXPRESSION_INVALID: cannot parse expression: {exc}",
            )
        )
        return ()

    fields: list[str] = []
    for column in parsed.find_all(exp.Column):
        if column.table:
            issues.append(
                MetricCompilerIssue(
                    definition.path,
                    "DIMENSION_QUALIFIED_FIELD_UNSUPPORTED: global and Cube "
                    "dimensions must reference base_object fields without a "
                    f"dataset qualifier: {column.sql(dialect=dialect)}",
                )
            )
        if column.name and column.name not in fields:
            fields.append(column.name)
    return tuple(fields)


def _public_dimension(dimension: dict[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(value)
        for key, value in dimension.items()
        if key in _DIMENSION_RUNTIME_FIELDS
    }


def _global_dimension(dimension: dict[str, Any]) -> dict[str, Any]:
    """Preserve graph-only source metadata without leaking it into Cube MDL."""

    return {
        key: deepcopy(value)
        for key, value in dimension.items()
        if key in _GLOBAL_DIMENSION_FIELDS
    }


def _repeated_inline_dimension_issues(
    cubes: list[dict],
) -> list[MetricCompilerIssue]:
    first_definition: dict[str, tuple[str, str]] = {}
    issues: list[MetricCompilerIssue] = []
    for cube_index, cube in enumerate(cubes):
        source = cube.get("_source_file", f"cubes[{cube_index}]")
        cube_name = cube.get("name", f"cubes[{cube_index}]")
        for member_key in _CUBE_DIMENSION_KEYS:
            members = cube.get(member_key) or []
            if not isinstance(members, list):
                continue
            for member_index, member in enumerate(members):
                if not isinstance(member, dict):
                    continue
                name = member.get("name")
                if not isinstance(name, str) or not name:
                    continue
                path = f"cubes/{source} > {cube_name} > {member_key}[{member_index}]"
                folded = name.casefold()
                previous = first_definition.get(folded)
                if previous is None:
                    first_definition[folded] = (name, path)
                    continue
                previous_name, previous_path = previous
                issues.append(
                    MetricCompilerIssue(
                        path,
                        "CUBE_DIMENSION_REPEATED_INLINE: inline dimension "
                        f"'{name}' is already defined at {previous_path}; define "
                        f"'{previous_name}' once under "
                        f"dimensions/{previous_name}/metadata.yml and reference "
                        "it by name from both Cubes",
                    )
                )
    return issues


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        folded = value.casefold()
        if folded in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(folded)
    return duplicates


def _deduplicate_issues(
    issues: list[MetricCompilerIssue],
) -> list[MetricCompilerIssue]:
    unique: list[MetricCompilerIssue] = []
    seen: set[tuple[str, str]] = set()
    for issue in issues:
        key = (issue.path, issue.message)
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    return unique

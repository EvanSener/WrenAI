"""Compile project-level metrics into the Cube measures understood by Wren MDL.

Global metrics are authored once under ``metrics/<name>/metadata.yml``.  Cubes
reference them by stable name.  The runtime MDL wire format remains unchanged:
before emitting ``target/mdl.json`` this module expands the references back into
the existing inline measure objects.

Compilation is deliberately strict.  Every metric expression is parsed into an
AST, derived-metric dependencies are expanded recursively, and every resulting
atomic field must exist on the Cube's ``base_object``.  This prevents a project
from producing a syntactically valid MDL that can never produce executable SQL.
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

from wren.mdl.cte_rewriter import get_sqlglot_dialect
from wren.model.data_source import DataSource

_METRIC_FIELDS = frozenset(
    {
        "name",
        "expression",
        "type",
        "label",
        "description",
        "synonyms",
    }
)
_REQUIRED_METRIC_FIELDS = ("name", "expression", "type")


@dataclass(frozen=True)
class MetricCompilerIssue:
    """A deterministic project source error found while compiling metrics."""

    path: str
    message: str


class MetricCompilationError(ValueError):
    """Raised when global metric references cannot be compiled safely."""

    def __init__(self, issues: list[MetricCompilerIssue]):
        self.issues = tuple(issues)
        details = "\n".join(f"- {issue.path}: {issue.message}" for issue in issues)
        super().__init__(f"Metric compilation failed:\n{details}")


@dataclass(frozen=True)
class _MetricDefinition:
    data: dict[str, Any]
    path: str
    is_global: bool


@dataclass(frozen=True)
class _ExpressionReferences:
    dependencies: tuple[str, ...]
    atomic_fields: tuple[str, ...]
    qualified_fields: tuple[str, ...]


def load_metrics(project_path: Path) -> list[dict]:
    """Load ``metrics/<name>/metadata.yml`` in deterministic name order."""

    metrics_dir = project_path / "metrics"
    if not metrics_dir.is_dir():
        return []

    metrics: list[dict] = []
    for directory in sorted(metrics_dir.iterdir()):
        if not directory.is_dir():
            continue
        metadata_file = directory / "metadata.yml"
        if not metadata_file.exists():
            continue
        metric = yaml.safe_load(metadata_file.read_text(encoding="utf-8")) or {}
        if not isinstance(metric, dict):
            metric = {"_invalid_value": metric}
        metric["_source_file"] = str(metadata_file.relative_to(metrics_dir))
        metrics.append(metric)
    return metrics


def compile_cube_metrics(
    *,
    cubes: list[dict],
    metrics: list[dict],
    models: list[dict],
    views: list[dict],
    data_source: str | None,
) -> list[dict]:
    """Expand global metric references and reject any invalid binding."""

    compiled, issues = analyze_cube_metrics(
        cubes=cubes,
        metrics=metrics,
        models=models,
        views=views,
        data_source=data_source,
    )
    if issues:
        raise MetricCompilationError(issues)
    return compiled


def analyze_cube_metrics(
    *,
    cubes: list[dict],
    metrics: list[dict],
    models: list[dict],
    views: list[dict],
    data_source: str | None,
) -> tuple[list[dict], list[MetricCompilerIssue]]:
    """Return compiled Cubes plus all metric compilation issues.

    This non-raising form is used by ``wren context validate`` so one run can
    report every bad Cube/metric binding instead of stopping at the first one.
    """

    issues: list[MetricCompilerIssue] = []
    global_definitions = _global_metric_registry(metrics, issues)
    dialect = dialect_for(data_source)
    global_names = {name.casefold(): name for name in global_definitions}
    global_resolver = _MetricDependencyResolver(
        definitions=global_definitions,
        canonical_names=global_names,
        global_names=global_names,
        dialect=dialect,
        issues=issues,
    )
    for metric_name in global_definitions:
        global_resolver.resolve(metric_name)

    issues.extend(_repeated_inline_metric_issues(cubes))
    object_fields = ObjectFieldResolver(models, views, dialect)
    compiled_cubes: list[dict] = []

    for index, original_cube in enumerate(cubes):
        cube = deepcopy(original_cube)
        source = cube.get("_source_file", f"cubes[{index}]")
        cube_path = f"cubes/{source}"
        cube_name = cube.get("name")
        if not isinstance(cube_name, str) or not cube_name:
            compiled_cubes.append(cube)
            continue

        raw_measures = cube.get("measures") or []
        if not isinstance(raw_measures, list):
            issues.append(
                MetricCompilerIssue(
                    f"{cube_path} > {cube_name} > measures",
                    "CUBE_METRICS_INVALID: measures must be a list",
                )
            )
            compiled_cubes.append(cube)
            continue

        definitions = dict(global_definitions)
        canonical_names = {name.casefold(): name for name in definitions}
        requested: list[str] = []

        for measure_index, raw_measure in enumerate(raw_measures):
            measure_path = f"{cube_path} > {cube_name} > measures[{measure_index}]"
            if isinstance(raw_measure, str):
                reference = raw_measure.strip()
                canonical = canonical_names.get(reference.casefold())
                if not reference or canonical is None:
                    issues.append(
                        MetricCompilerIssue(
                            measure_path,
                            "CUBE_METRIC_NOT_FOUND: "
                            f"global metric '{raw_measure}' is not defined under metrics/",
                        )
                    )
                    continue
                requested.append(canonical)
                continue

            if not isinstance(raw_measure, dict):
                issues.append(
                    MetricCompilerIssue(
                        measure_path,
                        "CUBE_METRIC_INVALID: measure must be a global metric name "
                        "or an inline measure object",
                    )
                )
                continue

            inline = deepcopy(raw_measure)
            inline_name = inline.get("name")
            _validate_metric_shape(inline, measure_path, issues)
            if not isinstance(inline_name, str) or not inline_name:
                continue
            folded = inline_name.casefold()
            if folded in canonical_names:
                existing = canonical_names[folded]
                issues.append(
                    MetricCompilerIssue(
                        measure_path,
                        "CUBE_METRIC_SHADOWED: inline measure "
                        f"'{inline_name}' conflicts with metric '{existing}'",
                    )
                )
                continue
            canonical_names[folded] = inline_name
            definitions[inline_name] = _MetricDefinition(
                inline, measure_path, is_global=False
            )
            requested.append(inline_name)

        duplicate_refs = _duplicates(requested)
        for duplicate in duplicate_refs:
            issues.append(
                MetricCompilerIssue(
                    f"{cube_path} > {cube_name} > measures",
                    f"CUBE_METRIC_DUPLICATE: metric '{duplicate}' is referenced more than once",
                )
            )

        resolver = _MetricDependencyResolver(
            definitions=definitions,
            canonical_names=canonical_names,
            global_names=global_names,
            dialect=dialect,
            issues=issues,
        )

        emitted: list[str] = []
        emitted_set: set[str] = set()
        required_fields: dict[str, tuple[str, ...]] = {}
        for metric_name in requested:
            resolution = resolver.resolve(metric_name)
            for dependency_name in resolution.order:
                if dependency_name not in emitted_set:
                    emitted.append(dependency_name)
                    emitted_set.add(dependency_name)
            for field_name, dependency_path in resolution.fields.items():
                required_fields.setdefault(field_name, dependency_path)

        base_object = cube.get("base_object")
        if required_fields and isinstance(base_object, str) and base_object:
            available, field_issues = object_fields.fields_for(base_object, cube_path)
            issues.extend(field_issues)
            if available is not None:
                available_folded = {field.casefold() for field in available}
                for field_name, dependency_path in sorted(required_fields.items()):
                    if field_name.casefold() in available_folded:
                        continue
                    issues.append(
                        MetricCompilerIssue(
                            f"{cube_path} > {cube_name} > measure '{dependency_path[0]}'",
                            "CUBE_METRIC_FIELD_MISSING: "
                            f"base_object '{base_object}' does not expose atomic field "
                            f"'{field_name}' (dependency path: "
                            f"{' -> '.join(dependency_path)} -> {field_name})",
                        )
                    )

        cube["measures"] = [
            _public_metric(definitions[name].data)
            for name in emitted
            if name in definitions
        ]
        compiled_cubes.append(cube)

    return compiled_cubes, _deduplicate_issues(issues)


def _global_metric_registry(
    metrics: list[dict], issues: list[MetricCompilerIssue]
) -> dict[str, _MetricDefinition]:
    definitions: dict[str, _MetricDefinition] = {}
    canonical_names: dict[str, str] = {}

    for index, metric in enumerate(metrics):
        source = metric.get("_source_file", f"metrics[{index}]")
        path = f"metrics/{source}"
        _validate_metric_shape(metric, path, issues)
        name = metric.get("name")
        if not isinstance(name, str) or not name:
            continue

        directory_name = Path(str(source)).parts[0]
        if directory_name != name:
            issues.append(
                MetricCompilerIssue(
                    path,
                    "METRIC_DIRECTORY_MISMATCH: directory name "
                    f"'{directory_name}' must match metric name '{name}'",
                )
            )

        folded = name.casefold()
        if folded in canonical_names:
            issues.append(
                MetricCompilerIssue(
                    path,
                    f"METRIC_DUPLICATE: metric name '{name}' is already defined",
                )
            )
            continue
        canonical_names[folded] = name
        definitions[name] = _MetricDefinition(
            _public_metric(metric), path, is_global=True
        )

    return definitions


def _validate_metric_shape(
    metric: dict[str, Any], path: str, issues: list[MetricCompilerIssue]
) -> None:
    if "_invalid_value" in metric:
        issues.append(
            MetricCompilerIssue(
                path,
                "METRIC_INVALID: metadata.yml must contain a YAML object",
            )
        )
        return

    for field_name in _REQUIRED_METRIC_FIELDS:
        value = metric.get(field_name)
        if not isinstance(value, str) or not value.strip():
            issues.append(
                MetricCompilerIssue(
                    path,
                    f"METRIC_FIELD_REQUIRED: '{field_name}' must be a non-empty string",
                )
            )

    unknown = sorted(
        key for key in metric if not key.startswith("_") and key not in _METRIC_FIELDS
    )
    if unknown:
        issues.append(
            MetricCompilerIssue(
                path,
                "METRIC_FIELD_UNKNOWN: unsupported field(s): " + ", ".join(unknown),
            )
        )

    synonyms = metric.get("synonyms")
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
                    "METRIC_SYNONYMS_INVALID: synonyms must be a unique list of "
                    "non-empty strings",
                )
            )


def _public_metric(metric: dict[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(value) for key, value in metric.items() if key in _METRIC_FIELDS
    }


def dialect_for(data_source: str | None) -> str | None:
    """Return the SQLGlot dialect used by semantic expression compilers."""

    if not isinstance(data_source, str) or not data_source:
        return None
    try:
        return get_sqlglot_dialect(DataSource(data_source.lower()))
    except ValueError:
        return None


def _parse_expression_references(
    definition: _MetricDefinition,
    canonical_names: dict[str, str],
    dialect: str | None,
    issues: list[MetricCompilerIssue],
) -> _ExpressionReferences:
    expression = definition.data.get("expression")
    if not isinstance(expression, str) or not expression.strip():
        return _ExpressionReferences((), (), ())

    try:
        parsed = sqlglot.parse_one(expression, dialect=dialect)
    except (ParseError, ValueError) as exc:
        issues.append(
            MetricCompilerIssue(
                definition.path,
                f"METRIC_EXPRESSION_INVALID: cannot parse expression: {exc}",
            )
        )
        return _ExpressionReferences((), (), ())

    dependencies: list[str] = []
    atomic_fields: list[str] = []
    qualified_fields: list[str] = []
    for column in parsed.find_all(exp.Column):
        name = column.name
        if not name:
            continue
        dependency = canonical_names.get(name.casefold())
        if dependency is not None and not column.table:
            if dependency not in dependencies:
                dependencies.append(dependency)
            continue
        if column.table:
            qualified = column.sql(dialect=dialect)
            if qualified not in qualified_fields:
                qualified_fields.append(qualified)
        if name not in atomic_fields:
            atomic_fields.append(name)

    return _ExpressionReferences(
        tuple(dependencies), tuple(atomic_fields), tuple(qualified_fields)
    )


@dataclass(frozen=True)
class _MetricResolution:
    order: tuple[str, ...]
    fields: dict[str, tuple[str, ...]]


class _MetricDependencyResolver:
    def __init__(
        self,
        *,
        definitions: dict[str, _MetricDefinition],
        canonical_names: dict[str, str],
        global_names: dict[str, str],
        dialect: str | None,
        issues: list[MetricCompilerIssue],
    ) -> None:
        self._definitions = definitions
        self._canonical_names = canonical_names
        self._global_names = global_names
        self._dialect = dialect
        self._issues = issues
        self._parsed: dict[str, _ExpressionReferences] = {}
        self._resolved: dict[str, _MetricResolution] = {}
        self._visiting: list[str] = []

    def resolve(self, name: str) -> _MetricResolution:
        if name in self._resolved:
            return self._resolved[name]
        if name in self._visiting:
            start = self._visiting.index(name)
            cycle = self._visiting[start:] + [name]
            definition = self._definitions[name]
            self._issues.append(
                MetricCompilerIssue(
                    definition.path,
                    "METRIC_DEPENDENCY_CYCLE: " + " -> ".join(cycle),
                )
            )
            return _MetricResolution((), {})

        definition = self._definitions.get(name)
        if definition is None:
            return _MetricResolution((), {})

        self._visiting.append(name)
        references = self._parsed.get(name)
        if references is None:
            references = _parse_expression_references(
                definition,
                self._global_names if definition.is_global else self._canonical_names,
                self._dialect,
                self._issues,
            )
            self._parsed[name] = references

        for qualified in references.qualified_fields:
            self._issues.append(
                MetricCompilerIssue(
                    definition.path,
                    "METRIC_QUALIFIED_FIELD_UNSUPPORTED: global and Cube metrics "
                    f"must reference base_object fields without a dataset qualifier: {qualified}",
                )
            )

        order: list[str] = []
        fields: dict[str, tuple[str, ...]] = {
            field_name: (name,) for field_name in references.atomic_fields
        }
        for dependency in references.dependencies:
            child = self.resolve(dependency)
            for child_name in child.order:
                if child_name not in order:
                    order.append(child_name)
            for field_name, path in child.fields.items():
                fields.setdefault(field_name, (name, *path))

        if name not in order:
            order.append(name)
        self._visiting.pop()
        resolution = _MetricResolution(tuple(order), fields)
        self._resolved[name] = resolution
        return resolution


class ObjectFieldResolver:
    """Resolve declared Model fields or projected View output names."""

    def __init__(
        self, models: list[dict], views: list[dict], dialect: str | None
    ) -> None:
        self._models = {
            model.get("name"): model
            for model in models
            if isinstance(model.get("name"), str)
        }
        self._views = {
            view.get("name"): view
            for view in views
            if isinstance(view.get("name"), str)
        }
        self._dialect = dialect
        self._cache: dict[str, tuple[set[str] | None, list[MetricCompilerIssue]]] = {}

    def fields_for(
        self, base_object: str, cube_path: str
    ) -> tuple[set[str] | None, list[MetricCompilerIssue]]:
        if base_object in self._cache:
            return self._cache[base_object]

        if base_object in self._models:
            fields = {
                column.get("name")
                for column in self._models[base_object].get("columns") or []
                if isinstance(column, dict) and isinstance(column.get("name"), str)
            }
            result = (fields, [])
            self._cache[base_object] = result
            return result

        view = self._views.get(base_object)
        if view is None:
            return (None, [])

        statement = view.get("statement")
        path = f"views/{view.get('_source_dir', base_object)}"
        if not isinstance(statement, str) or not statement.strip():
            result = (
                None,
                [
                    MetricCompilerIssue(
                        f"{cube_path} > base_object '{base_object}'",
                        f"CUBE_BASE_OBJECT_FIELDS_UNKNOWN: {path} has no SQL statement",
                    )
                ],
            )
            return result

        try:
            parsed = sqlglot.parse_one(statement, dialect=self._dialect)
        except (ParseError, ValueError) as exc:
            result = (
                None,
                [
                    MetricCompilerIssue(
                        f"{cube_path} > base_object '{base_object}'",
                        "CUBE_BASE_OBJECT_FIELDS_UNKNOWN: cannot parse view SQL "
                        f"for field validation: {exc}",
                    )
                ],
            )
            return result

        selected = parsed.named_selects
        if not selected or any(name == "*" for name in selected):
            result = (
                None,
                [
                    MetricCompilerIssue(
                        f"{cube_path} > base_object '{base_object}'",
                        "CUBE_BASE_OBJECT_FIELDS_UNKNOWN: view must use explicit "
                        "SELECT projections (with aliases for computed fields); "
                        "wildcard projections cannot prove metric fields",
                    )
                ],
            )
            return result

        result = ({name for name in selected if name}, [])
        self._cache[base_object] = result
        return result


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        folded = value.casefold()
        if folded in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(folded)
    return duplicates


def _repeated_inline_metric_issues(
    cubes: list[dict],
) -> list[MetricCompilerIssue]:
    """Require a shared inline metric name to move into the global catalog."""

    first_definition: dict[str, tuple[str, str]] = {}
    issues: list[MetricCompilerIssue] = []
    for cube_index, cube in enumerate(cubes):
        source = cube.get("_source_file", f"cubes[{cube_index}]")
        cube_name = cube.get("name", f"cubes[{cube_index}]")
        measures = cube.get("measures") or []
        if not isinstance(measures, list):
            continue
        for measure_index, measure in enumerate(measures):
            if not isinstance(measure, dict):
                continue
            name = measure.get("name")
            if not isinstance(name, str) or not name:
                continue
            path = f"cubes/{source} > {cube_name} > measures[{measure_index}]"
            folded = name.casefold()
            previous = first_definition.get(folded)
            if previous is None:
                first_definition[folded] = (name, path)
                continue
            previous_name, previous_path = previous
            issues.append(
                MetricCompilerIssue(
                    path,
                    "CUBE_METRIC_REPEATED_INLINE: inline metric "
                    f"'{name}' is already defined at {previous_path}; define "
                    f"'{previous_name}' once under metrics/{previous_name}/metadata.yml "
                    "and reference it by name from both Cubes",
                )
            )
    return issues


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

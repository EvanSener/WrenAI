"""Detect and safely apply warehouse schema drift to Wren models.

Physical ``table_reference`` models are source-backed structural snapshots. This
module compares those snapshots with live warehouse metadata, preserves curated
MDL semantics, validates the complete candidate project in a temporary tree,
and only then replaces model files plus ``target/mdl.json``.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from wren.table_scaffold import (
    IntrospectedTable,
    merge_existing_semantics,
    model_metadata_from_table,
)

MIN_WATCH_INTERVAL_SECONDS = 1.0
_SOURCE_DIRECTORIES = (
    "models",
    "views",
    "metrics",
    "dimensions",
    "cubes",
    "knowledge",
)
_SOURCE_FILES = (
    "wren_project.yml",
    "relationships.yml",
    "instructions.md",
    "queries.yml",
    "views.yml",
)


@dataclass(frozen=True)
class SchemaChange:
    """One normalized difference between a source Model and its live table."""

    kind: str
    column: str | None = None
    before: Any = None
    after: Any = None
    breaking: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "column": self.column,
            "before": self.before,
            "after": self.after,
            "breaking": self.breaking,
        }


@dataclass(frozen=True)
class SchemaSyncIssue:
    model: str
    table: str | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "table": self.table,
            "message": self.message,
        }


@dataclass(frozen=True)
class SkippedModel:
    model: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"model": self.model, "reason": self.reason}


@dataclass
class ModelSyncPlan:
    model_name: str
    source_dir: str
    table_name: str
    metadata_path: Path
    existing: dict[str, Any]
    candidate: dict[str, Any]
    changes: list[SchemaChange] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        return bool(self.changes)

    @property
    def has_breaking(self) -> bool:
        return any(change.breaking for change in self.changes)

    def to_dict(self, project_path: Path) -> dict[str, Any]:
        return {
            "model": self.model_name,
            "table": self.table_name,
            "path": str(self.metadata_path.relative_to(project_path)),
            "changes": [change.to_dict() for change in self.changes],
            "breaking": self.has_breaking,
        }


@dataclass
class SchemaSyncPlan:
    project_path: Path
    models: list[ModelSyncPlan] = field(default_factory=list)
    skipped: list[SkippedModel] = field(default_factory=list)
    issues: list[SchemaSyncIssue] = field(default_factory=list)

    @property
    def changed_models(self) -> list[ModelSyncPlan]:
        return [model for model in self.models if model.has_drift]

    @property
    def changes(self) -> list[SchemaChange]:
        return [change for model in self.changed_models for change in model.changes]

    @property
    def breaking_changes(self) -> list[SchemaChange]:
        return [change for change in self.changes if change.breaking]

    @property
    def has_drift(self) -> bool:
        return bool(self.changed_models)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": str(self.project_path),
            "scannedModels": len(self.models),
            "changedModels": len(self.changed_models),
            "changes": len(self.changes),
            "breakingChanges": len(self.breaking_changes),
            "models": [
                model.to_dict(self.project_path) for model in self.changed_models
            ],
            "skipped": [item.to_dict() for item in self.skipped],
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class CandidateBuild:
    manifest: dict[str, Any] | None
    errors: tuple[str, ...] = ()


@dataclass
class SchemaSyncExecution:
    plan: SchemaSyncPlan
    candidate: CandidateBuild | None = None
    applied: bool = False
    written_files: tuple[Path, ...] = ()
    memory_result: dict[str, Any] | None = None
    memory_error: str | None = None

    @property
    def blocked(self) -> bool:
        return bool(
            self.plan.issues
            or self.plan.breaking_changes
            or (self.candidate and self.candidate.errors)
        )

    def to_dict(self) -> dict[str, Any]:
        payload = self.plan.to_dict()
        payload.update(
            {
                "applied": self.applied,
                "blocked": self.blocked,
                "validationErrors": list(self.candidate.errors)
                if self.candidate
                else [],
                "writtenFiles": [
                    str(path.relative_to(self.plan.project_path))
                    for path in self.written_files
                ],
                "memory": self.memory_result,
                "memoryError": self.memory_error,
            }
        )
        return payload


@dataclass
class SchemaWatchState:
    polls: int = 0
    applied: int = 0
    blocked: int = 0
    errors: int = 0


IntrospectTable = Callable[..., IntrospectedTable]


def plan_schema_sync(
    project_path: Path,
    introspect: IntrospectTable,
    *,
    selected_models: set[str] | None = None,
) -> SchemaSyncPlan:
    """Read every selected table-backed Model and calculate live schema drift."""
    project_path = project_path.resolve()
    plan = SchemaSyncPlan(project_path=project_path)
    models_dir = project_path / "models"
    discovered: set[str] = set()

    if not models_dir.is_dir():
        return plan

    for model_dir in sorted(path for path in models_dir.iterdir() if path.is_dir()):
        metadata_path = model_dir / "metadata.yml"
        if not metadata_path.is_file():
            continue
        try:
            existing = yaml.safe_load(metadata_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            plan.issues.append(
                SchemaSyncIssue(model_dir.name, None, f"invalid metadata YAML: {exc}")
            )
            continue
        if not isinstance(existing, dict):
            plan.issues.append(
                SchemaSyncIssue(
                    model_dir.name, None, "metadata.yml must contain a YAML object"
                )
            )
            continue

        model_name = str(existing.get("name") or model_dir.name)
        discovered.add(model_name)
        if selected_models and model_name not in selected_models:
            continue
        if existing.get("ref_sql") or (model_dir / "ref_sql.sql").is_file():
            plan.skipped.append(SkippedModel(model_name, "ref_sql model"))
            continue

        table_reference = existing.get("table_reference")
        if not isinstance(table_reference, dict):
            plan.issues.append(
                SchemaSyncIssue(
                    model_name,
                    None,
                    "table_reference model must define a table reference object",
                )
            )
            continue
        table_name = str(table_reference.get("table") or "").strip()
        if not table_name:
            plan.issues.append(
                SchemaSyncIssue(
                    model_name, None, "table_reference.table must not be empty"
                )
            )
            continue

        table_schema = _optional_string(table_reference.get("schema"))
        table_catalog = _optional_string(table_reference.get("catalog"))
        try:
            table = introspect(
                table_name,
                table_schema=table_schema,
                table_catalog=table_catalog,
            )
        except Exception as exc:  # noqa: BLE001 — isolate one remote table failure
            plan.issues.append(
                SchemaSyncIssue(model_name, table_name, str(exc) or type(exc).__name__)
            )
            continue

        generated = model_metadata_from_table(
            table,
            model_name=model_name,
            table_schema=table_schema,
            table_catalog=table_catalog,
        )
        candidate = merge_existing_semantics(generated, existing)
        changes = diff_model_schema(existing, candidate)
        plan.models.append(
            ModelSyncPlan(
                model_name=model_name,
                source_dir=model_dir.name,
                table_name=table_name,
                metadata_path=metadata_path,
                existing=existing,
                candidate=candidate,
                changes=changes,
            )
        )

    if selected_models:
        for missing in sorted(selected_models - discovered):
            plan.issues.append(
                SchemaSyncIssue(missing, None, "selected model was not found")
            )
    return plan


def diff_model_schema(
    existing: dict[str, Any], candidate: dict[str, Any]
) -> list[SchemaChange]:
    """Return deterministic physical schema differences for one Model."""
    before = _physical_columns(existing)
    after = _physical_columns(candidate)
    changes: list[SchemaChange] = []

    for name in sorted(after.keys() - before.keys()):
        changes.append(
            SchemaChange(
                kind="column_added",
                column=name,
                after=_column_type(after[name]),
            )
        )
    for name in sorted(before.keys() - after.keys()):
        changes.append(
            SchemaChange(
                kind="column_removed",
                column=name,
                before=_column_type(before[name]),
                breaking=True,
            )
        )
    for name in sorted(before.keys() & after.keys()):
        before_type = _column_type(before[name])
        after_type = _column_type(after[name])
        if before_type != after_type:
            changes.append(
                SchemaChange(
                    kind="column_type_changed",
                    column=name,
                    before=before_type,
                    after=after_type,
                    breaking=True,
                )
            )
        before_partition = _partition_signature(before[name])
        after_partition = _partition_signature(after[name])
        if before_partition != after_partition:
            changes.append(
                SchemaChange(
                    kind="partition_changed",
                    column=name,
                    before=before_partition,
                    after=after_partition,
                    breaking=True,
                )
            )

    before_order = [
        col.get("name")
        for col in existing.get("columns") or []
        if isinstance(col, dict) and not _is_semantic_column(col)
    ]
    after_order = [
        col.get("name")
        for col in candidate.get("columns") or []
        if isinstance(col, dict) and not _is_semantic_column(col)
    ]
    if set(before_order) == set(after_order) and before_order != after_order:
        changes.append(
            SchemaChange(
                kind="column_order_changed",
                before=before_order,
                after=after_order,
            )
        )

    before_comment = _table_comment(existing)
    after_comment = _table_comment(candidate)
    if before_comment != after_comment:
        changes.append(
            SchemaChange(
                kind="table_comment_changed",
                before=before_comment,
                after=after_comment,
            )
        )
    return changes


def build_candidate_project(project_path: Path, plan: SchemaSyncPlan) -> CandidateBuild:
    """Validate and compile candidate Model files without touching the project."""
    from wren.context import build_json, validate_project  # noqa: PLC0415

    with tempfile.TemporaryDirectory(prefix="wren-schema-sync-") as temp_dir:
        candidate_root = Path(temp_dir) / "project"
        candidate_root.mkdir()
        _copy_project_sources(project_path, candidate_root)
        for model in plan.changed_models:
            out = candidate_root / "models" / model.source_dir / "metadata.yml"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(_dump_yaml(model.candidate), encoding="utf-8")

        errors = validate_project(candidate_root)
        hard_errors = tuple(str(error) for error in errors if error.level == "error")
        if hard_errors:
            return CandidateBuild(manifest=None, errors=hard_errors)
        try:
            return CandidateBuild(manifest=build_json(candidate_root))
        except (OSError, RuntimeError, ValueError) as exc:
            return CandidateBuild(manifest=None, errors=(f"build failed: {exc}",))


def execute_schema_sync(
    project_path: Path,
    introspect: IntrospectTable,
    *,
    apply_additive: bool = False,
    selected_models: set[str] | None = None,
    reindex_memory: bool = True,
) -> SchemaSyncExecution:
    """Plan one sync, optionally apply a fully safe candidate, and reindex."""
    plan = plan_schema_sync(
        project_path,
        introspect,
        selected_models=selected_models,
    )
    execution = SchemaSyncExecution(plan=plan)
    if plan.issues or not plan.has_drift:
        return execution

    execution.candidate = build_candidate_project(project_path, plan)
    if not apply_additive or execution.blocked:
        return execution
    if execution.candidate.manifest is None:
        return execution

    execution.written_files = tuple(
        apply_candidate_project(project_path, plan, execution.candidate.manifest)
    )
    execution.applied = True

    if reindex_memory:
        try:
            execution.memory_result = reindex_project_memory(
                project_path, execution.candidate.manifest
            )
        except (
            ImportError,
            ModuleNotFoundError,
            OSError,
            RuntimeError,
            ValueError,
        ) as exc:
            execution.memory_error = str(exc) or type(exc).__name__
    return execution


def apply_candidate_project(
    project_path: Path,
    plan: SchemaSyncPlan,
    manifest: dict[str, Any],
) -> list[Path]:
    """Commit all changed Models and the compiled target with rollback on error."""
    payloads: list[tuple[Path, str]] = [
        (model.metadata_path, _dump_yaml(model.candidate))
        for model in plan.changed_models
    ]
    payloads.append(
        (
            project_path / "target" / "mdl.json",
            json.dumps(manifest, indent=2, ensure_ascii=False),
        )
    )
    _replace_payloads(payloads)
    return [path for path, _ in payloads]


def reindex_project_memory(
    project_path: Path, manifest: dict[str, Any]
) -> dict[str, Any] | None:
    """Refresh the project-local semantic schema index when LanceDB is enabled."""
    from wren.memory.index_backend import resolve_backend  # noqa: PLC0415

    if resolve_backend() == "grep":
        return None
    from wren.memory import WrenMemory  # noqa: PLC0415

    memory = WrenMemory(project_path / ".wren" / "memory")
    return memory.index_manifest(manifest)


def watch_schema_sync(
    sync_once: Callable[[], SchemaSyncExecution],
    *,
    interval: float = 300.0,
    max_polls: int | None = None,
    on_result: Callable[[SchemaSyncExecution], None] | None = None,
    on_error: Callable[[Exception], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> SchemaWatchState:
    """Poll the warehouse and apply safe drift until interrupted."""
    state = SchemaWatchState()
    interval = max(float(interval), MIN_WATCH_INTERVAL_SECONDS)
    try:
        while max_polls is None or state.polls < max_polls:
            state.polls += 1
            try:
                result = sync_once()
                if result.applied:
                    state.applied += 1
                if result.blocked:
                    state.blocked += 1
                if on_result:
                    on_result(result)
            except Exception as exc:  # noqa: BLE001 — watcher must retry transient errors
                state.errors += 1
                if on_error:
                    on_error(exc)
            if max_polls is not None and state.polls >= max_polls:
                break
            sleep(interval)
    except KeyboardInterrupt:
        pass
    return state


def _copy_project_sources(project_path: Path, candidate_root: Path) -> None:
    for name in _SOURCE_DIRECTORIES:
        source = project_path / name
        if source.is_dir():
            shutil.copytree(source, candidate_root / name, symlinks=True)
    for name in _SOURCE_FILES:
        source = project_path / name
        if source.is_file():
            shutil.copy2(source, candidate_root / name)


def _replace_payloads(payloads: list[tuple[Path, str]]) -> None:
    originals: dict[Path, bytes | None] = {}
    staged: dict[Path, Path] = {}
    replaced: list[Path] = []
    try:
        for path, content in payloads:
            path.parent.mkdir(parents=True, exist_ok=True)
            originals[path] = path.read_bytes() if path.exists() else None
            staged[path] = _stage_text(path, content)
        for path, _ in payloads:
            os.replace(staged[path], path)
            replaced.append(path)
    except Exception:
        for path in reversed(replaced):
            original = originals[path]
            if original is None:
                path.unlink(missing_ok=True)
            else:
                os.replace(_stage_bytes(path, original), path)
        raise
    finally:
        for temp_path in staged.values():
            temp_path.unlink(missing_ok=True)


def _stage_text(destination: Path, content: str) -> Path:
    return _stage_bytes(destination, content.encode("utf-8"))


def _stage_bytes(destination: Path, content: bytes) -> Path:
    fd, raw_path = tempfile.mkstemp(
        prefix=f".{destination.name}.schema-sync-",
        suffix=".tmp",
        dir=destination.parent,
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        Path(raw_path).unlink(missing_ok=True)
        raise
    return Path(raw_path)


def _physical_columns(metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(column["name"]): column
        for column in metadata.get("columns") or []
        if isinstance(column, dict)
        and column.get("name")
        and not _is_semantic_column(column)
    }


def _is_semantic_column(column: dict[str, Any]) -> bool:
    return bool(
        column.get("is_calculated")
        or column.get("isCalculated")
        or column.get("relationship")
    )


def _column_type(column: dict[str, Any]) -> str:
    return str(column.get("type") or "").strip().upper()


def _partition_signature(column: dict[str, Any]) -> dict[str, Any]:
    properties = column.get("properties") or {}
    if not isinstance(properties, dict):
        properties = {}
    is_partition = bool(properties.get("is_partition") or properties.get("isPartition"))
    default = properties.get("partition_default")
    if default is None:
        default = properties.get("partitionDefault")
    return {"isPartition": is_partition, "default": default}


def _table_comment(metadata: dict[str, Any]) -> str:
    table_reference = metadata.get("table_reference") or {}
    if not isinstance(table_reference, dict):
        return ""
    return str(table_reference.get("description") or "")


def _optional_string(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _dump_yaml(value: dict[str, Any]) -> str:
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False)

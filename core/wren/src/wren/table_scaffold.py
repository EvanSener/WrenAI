"""Scaffold Wren model YAML from live database metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_MODEL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_]+")
_LATEST_PARTITION_COLUMN = "ds"


@dataclass(frozen=True)
class IntrospectedColumn:
    name: str
    type: str
    comment: str | None = None
    is_partition: bool = False


@dataclass(frozen=True)
class IntrospectedTable:
    physical_table: str
    columns: list[IntrospectedColumn]
    comment: str | None = None


def default_model_name(physical_table: str) -> str:
    """Derive a stable Wren model name from a physical table name."""
    raw = physical_table.rsplit(".", 1)[-1].strip()
    name = _SAFE_NAME_RE.sub("_", raw).strip("_").lower()
    if not name:
        name = "model"
    if name[0].isdigit():
        name = f"model_{name}"
    return name


def validate_model_name(name: str) -> None:
    if not _MODEL_NAME_RE.match(name):
        raise ValueError(
            "model name must match [A-Za-z_][A-Za-z0-9_]*; "
            f"got {name!r}"
        )


def model_metadata_from_table(
    table: IntrospectedTable,
    *,
    model_name: str,
    description: str | None = None,
    table_schema: str | None = None,
    table_catalog: str | None = None,
) -> dict[str, Any]:
    """Build a ``models/<name>/metadata.yml`` payload."""
    validate_model_name(model_name)
    partition_columns = [col.name for col in table.columns if col.is_partition]
    latest_partition_column = next(
        (
            name
            for name in partition_columns
            if name.lower() == _LATEST_PARTITION_COLUMN
        ),
        None,
    )
    model_description = description or table.comment
    if not model_description:
        model_description = (
            f"MaxCompute 表 {table.physical_table} 的语义模型。请补充业务口径。"
        )
    if partition_columns and "分区" not in model_description:
        model_description = (
            f"{model_description} 按 {', '.join(partition_columns)} 分区。"
        )

    table_reference: dict[str, str] = {"table": table.physical_table}
    if table_catalog:
        table_reference["catalog"] = table_catalog
    if table_schema:
        table_reference["schema"] = table_schema

    columns: list[dict[str, Any]] = []
    for col in table.columns:
        col_desc = col.comment or f"MaxCompute 字段 {col.name}。请补充业务含义。"
        col_properties: dict[str, Any] = {"description": col_desc}
        if col.is_partition:
            suffix = "分区字段；未指定时默认查询最新分区。"
            col_desc = f"{col_desc.rstrip('。')}；{suffix}"
            col_properties["description"] = col_desc
            col_properties["is_partition"] = True
            if col.name == latest_partition_column:
                col_properties["partition_default"] = "max_pt"
        columns.append(
            {
                "name": col.name,
                "type": col.type,
                "properties": col_properties,
            }
        )

    model_properties: dict[str, Any] = {
        "description": model_description,
        "unique_identifier": "",
        "unique_identifier_meaning": "",
    }
    if partition_columns:
        model_properties["partition_columns"] = partition_columns
    if latest_partition_column:
        model_properties["default_partition_filter"] = {
            "column": latest_partition_column,
            "expression": f"{latest_partition_column} = max_pt('{table.physical_table}')",
        }

    return {
        "name": model_name,
        "properties": model_properties,
        "table_reference": table_reference,
        "columns": columns,
    }


def merge_existing_semantics(
    metadata: dict[str, Any],
    existing: dict[str, Any],
    *,
    preserve_descriptions: bool = True,
) -> dict[str, Any]:
    """Merge curated semantic properties from an existing model.

    Refreshing a live table should update structural metadata while preserving
    business wording analysts already curated. Generated partition metadata wins
    because it is derived from the live warehouse schema.
    """
    if not isinstance(existing, dict):
        return metadata

    merged = dict(metadata)
    existing_props = existing.get("properties") or {}
    new_props = metadata.get("properties") or {}
    if isinstance(existing_props, dict):
        model_props = _merge_properties(existing_props, new_props)
        if preserve_descriptions and existing_props.get("description"):
            model_props["description"] = existing_props["description"]
        merged["properties"] = model_props

    existing_cols = {
        col.get("name"): col
        for col in existing.get("columns", [])
        if isinstance(col, dict) and col.get("name")
    }
    merged_cols: list[dict[str, Any]] = []
    for col in metadata.get("columns", []):
        if not isinstance(col, dict):
            merged_cols.append(col)
            continue
        existing_col = existing_cols.get(col.get("name")) or {}
        existing_col_props = existing_col.get("properties") or {}
        new_col_props = col.get("properties") or {}
        if isinstance(existing_col_props, dict):
            col = dict(col)
            col_props = _merge_properties(existing_col_props, new_col_props)
            if preserve_descriptions and existing_col_props.get("description"):
                col_props["description"] = existing_col_props["description"]
            col["properties"] = col_props
        merged_cols.append(col)
    merged["columns"] = merged_cols
    return merged


def _merge_properties(existing: dict[str, Any], generated: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in generated.items():
        if value in (None, "") and existing.get(key) not in (None, ""):
            continue
        merged[key] = value
    return merged


def write_model_metadata(
    project_path: Path,
    metadata: dict[str, Any],
    *,
    force: bool = False,
) -> Path:
    """Write model metadata and refuse accidental overwrite by default."""
    model_name = metadata["name"]
    validate_model_name(model_name)
    model_dir = project_path / "models" / model_name
    out = model_dir / "metadata.yml"
    if out.exists() and not force:
        raise FileExistsError(
            f"{out} already exists. Pass --force to overwrite this model."
        )
    model_dir.mkdir(parents=True, exist_ok=True)
    out.write_text(
        yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return out


def _odps_field_comment(field: Any) -> str | None:
    comment = getattr(field, "comment", None)
    if comment is None:
        return None
    comment = str(comment).strip()
    return comment or None


def _odps_field_type(field: Any) -> str:
    raw_type = getattr(field, "type", "")
    type_str = str(raw_type).strip()
    return type_str.upper() if type_str else "STRING"


def introspect_maxcompute_table(
    connection_info: Any,
    table_name: str,
    *,
    table_schema: str | None = None,
) -> IntrospectedTable:
    """Read MaxCompute table metadata through PyODPS.

    ``connection_info`` is intentionally typed loosely to avoid importing the
    Pydantic model at module import time; callers pass ``MaxComputeConnectionInfo``.
    """
    try:
        from odps import ODPS  # noqa: PLC0415
    except ImportError as e:
        raise RuntimeError(
            "MaxCompute table scaffolding requires pyodps. "
            "Install the maxcompute extra for this Wren CLI environment."
        ) from e

    kwargs: dict[str, Any] = {
        "project": connection_info.project,
        "endpoint": connection_info.endpoint,
    }
    if connection_info.schema_name:
        kwargs["schema"] = connection_info.schema_name
    if connection_info.tunnel_endpoint:
        kwargs["tunnel_endpoint"] = connection_info.tunnel_endpoint
    if connection_info.quota_name:
        kwargs["quota_name"] = connection_info.quota_name

    odps = ODPS(
        connection_info.access_id.get_secret_value(),
        connection_info.access_key.get_secret_value(),
        **kwargs,
    )
    get_table_kwargs: dict[str, Any] = {}
    effective_schema = table_schema or connection_info.schema_name
    if effective_schema:
        get_table_kwargs["schema"] = effective_schema
    try:
        table = odps.get_table(table_name, **get_table_kwargs)
    except TypeError:
        table = odps.get_table(table_name)

    schema_obj = getattr(table, "table_schema", None) or getattr(table, "schema", None)
    if schema_obj is None:
        raise RuntimeError(f"Could not read schema for MaxCompute table {table_name!r}.")

    partition_names = {
        getattr(partition, "name", "")
        for partition in getattr(schema_obj, "partitions", [])
    }
    columns_by_name: dict[str, IntrospectedColumn] = {}
    for field in list(getattr(schema_obj, "columns", [])):
        name = str(getattr(field, "name", "")).strip()
        if not name:
            continue
        columns_by_name[name] = IntrospectedColumn(
            name=name,
            type=_odps_field_type(field),
            comment=_odps_field_comment(field),
            is_partition=name in partition_names,
        )
    for field in list(getattr(schema_obj, "partitions", [])):
        name = str(getattr(field, "name", "")).strip()
        if not name:
            continue
        existing = columns_by_name.get(name)
        if existing:
            columns_by_name[name] = IntrospectedColumn(
                name=existing.name,
                type=existing.type,
                comment=existing.comment or _odps_field_comment(field),
                is_partition=True,
            )
        else:
            columns_by_name[name] = IntrospectedColumn(
                name=name,
                type=_odps_field_type(field),
                comment=_odps_field_comment(field),
                is_partition=True,
            )
    columns = list(columns_by_name.values())
    if not columns:
        raise RuntimeError(f"MaxCompute table {table_name!r} has no readable columns.")

    table_comment = getattr(table, "comment", None)
    if table_comment is not None:
        table_comment = str(table_comment).strip() or None

    return IntrospectedTable(
        physical_table=table_name,
        columns=columns,
        comment=table_comment,
    )

"""Scaffold Wren model YAML from live database metadata."""

from __future__ import annotations

import re
from copy import deepcopy
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
    """Derive the Wren model name from table_reference.table."""
    raw = physical_table.rsplit(".", 1)[-1].strip()
    name = _SAFE_NAME_RE.sub("_", raw).strip("_")
    if not name:
        name = "model"
    if name[0].isdigit():
        name = f"model_{name}"
    return name


def validate_model_name(name: str) -> None:
    if not _MODEL_NAME_RE.match(name):
        raise ValueError(f"model name must match [A-Za-z_][A-Za-z0-9_]*; got {name!r}")


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
    partition_columns = [col for col in table.columns if col.is_partition]
    latest_partition_column = next(
        (
            col.name
            for col in partition_columns
            if col.name.lower() == _LATEST_PARTITION_COLUMN
        ),
        None,
    )

    table_reference: dict[str, str] = {
        "table": table.physical_table,
        "description": table.comment or "",
    }
    if table_catalog:
        table_reference["catalog"] = table_catalog
    if table_schema:
        table_reference["schema"] = table_schema

    columns: list[dict[str, Any]] = []
    for col in table.columns:
        col_desc = _column_description(col, latest_partition_column)
        col_properties: dict[str, Any] = {"description": col_desc}
        if col.is_partition:
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
        "description": description or "",
        "flag": "",
        "row_description": "",
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
    business wording analysts already curated. Generated field-level partition
    metadata wins because it is derived from the live warehouse schema.
    """
    if not isinstance(existing, dict):
        return metadata

    merged = dict(metadata)
    for key, value in existing.items():
        if key.startswith("_") or key in {
            "name",
            "ref_sql",
            "table_reference",
            "columns",
            "properties",
        }:
            continue
        merged[key] = deepcopy(value)

    existing_table_reference = existing.get("table_reference") or {}
    generated_table_reference = metadata.get("table_reference") or {}
    if isinstance(existing_table_reference, dict) and isinstance(
        generated_table_reference, dict
    ):
        table_reference = dict(generated_table_reference)
        for key, value in existing_table_reference.items():
            if key not in {"table", "catalog", "schema", "description"}:
                table_reference[key] = deepcopy(value)
        if preserve_descriptions and existing_table_reference.get("description"):
            table_reference["description"] = existing_table_reference["description"]
        merged["table_reference"] = table_reference
    existing_props = existing.get("properties") or {}
    new_props = metadata.get("properties") or {}
    row_unique_names = _row_unique_identifier_names_from_model_properties(
        existing_props
    )
    if isinstance(existing_props, dict):
        model_props = _merge_properties(existing_props, new_props)
        legacy_meaning = _first_property_value(
            existing_props,
            "unique_identifier_meaning",
            "uniqueIdentifierMeaning",
        )
        if legacy_meaning and not model_props.get("row_description"):
            model_props["row_description"] = legacy_meaning
        if preserve_descriptions and existing_props.get("description"):
            model_props["description"] = existing_props["description"]
        _drop_deprecated_model_properties(model_props)
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
        if isinstance(existing_col, dict):
            col = dict(col)
            for key, value in existing_col.items():
                if key not in {"name", "type", "properties"}:
                    col[key] = deepcopy(value)
        if isinstance(existing_col_props, dict):
            col_props = _merge_properties(existing_col_props, new_col_props)
            if preserve_descriptions and existing_col_props.get("description"):
                col_props["description"] = existing_col_props["description"]
            if col.get("name") in row_unique_names:
                col_props["is_row_unique_id"] = True
            _drop_deprecated_column_properties(col_props)
            col["properties"] = col_props
        merged_cols.append(col)
    generated_names = {col.get("name") for col in merged_cols if isinstance(col, dict)}
    for name, existing_col in existing_cols.items():
        if name not in generated_names and _is_semantic_only_column(existing_col):
            merged_cols.append(deepcopy(existing_col))
    merged["columns"] = merged_cols
    return merged


def _is_semantic_only_column(column: dict[str, Any]) -> bool:
    """Return whether a column is defined by MDL rather than the source table."""
    return bool(
        column.get("is_calculated")
        or column.get("isCalculated")
        or column.get("relationship")
    )


def _column_description(
    col: IntrospectedColumn,
    latest_partition_column: str | None,
) -> str:
    if col.is_partition:
        description = col.comment or f"MaxCompute 分区字段 {col.name}。请补充业务含义。"
    else:
        description = col.comment or f"MaxCompute 字段 {col.name}。请补充业务含义。"
    if (
        col.is_partition
        and col.name == latest_partition_column
        and "默认查询最新分区" not in description
    ):
        description = f"{description.rstrip('。')}；未指定时默认查询最新分区。"
    return description


def _drop_deprecated_model_properties(properties: dict[str, Any]) -> None:
    for key in (
        "partition_columns",
        "partitionColumns",
        "default_partition_filter",
        "defaultPartitionFilter",
        "unique_identifier",
        "uniqueIdentifier",
        "unique_identifier_columns",
        "uniqueIdentifierColumns",
        "unique_identifier_meaning",
        "uniqueIdentifierMeaning",
    ):
        properties.pop(key, None)


def _drop_deprecated_column_properties(properties: dict[str, Any]) -> None:
    for old_key, new_key in (
        ("is_row_unique_identifier", "is_row_unique_id"),
        ("isRowUniqueIdentifier", "isRowUniqueId"),
    ):
        old_value = properties.pop(old_key, None)
        if old_value not in (None, "") and new_key not in properties:
            properties[new_key] = old_value


def _merge_properties(
    existing: dict[str, Any], generated: dict[str, Any]
) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in generated.items():
        if value in (None, "") and existing.get(key) not in (None, ""):
            continue
        merged[key] = value
    return merged


def _first_property_value(properties: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = properties.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _row_unique_identifier_names_from_model_properties(
    properties: Any,
) -> set[str]:
    if not isinstance(properties, dict):
        return set()
    names: set[str] = set()
    for key in ("unique_identifier_columns", "uniqueIdentifierColumns"):
        names.update(_unique_identifier_column_names(properties.get(key)))
    for key in ("unique_identifier", "uniqueIdentifier"):
        names.update(_unique_identifier_column_names(properties.get(key)))
    return names


def _unique_identifier_column_names(value: Any) -> set[str]:
    if isinstance(value, str):
        return {part.strip() for part in value.split(",") if part.strip()}
    if isinstance(value, list):
        names: set[str] = set()
        for item in value:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
            else:
                name = str(item).strip()
            if name:
                names.add(name)
        return names
    return set()


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


def create_maxcompute_client(connection_info: Any) -> Any:
    """Create one reusable PyODPS client from Wren connection information."""
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

    return ODPS(
        connection_info.access_id.get_secret_value(),
        connection_info.access_key.get_secret_value(),
        **kwargs,
    )


def introspect_maxcompute_table(
    connection_info: Any,
    table_name: str,
    *,
    table_schema: str | None = None,
    table_catalog: str | None = None,
    client: Any | None = None,
) -> IntrospectedTable:
    """Read MaxCompute table metadata through PyODPS.

    ``connection_info`` is intentionally typed loosely to avoid importing the
    Pydantic model at module import time; callers pass ``MaxComputeConnectionInfo``.
    """
    odps = client or create_maxcompute_client(connection_info)
    get_table_kwargs: dict[str, Any] = {}
    effective_schema = table_schema or connection_info.schema_name
    if effective_schema:
        get_table_kwargs["schema"] = effective_schema
    if table_catalog:
        get_table_kwargs["project"] = table_catalog
    try:
        table = odps.get_table(table_name, **get_table_kwargs)
    except TypeError:
        table = odps.get_table(table_name)

    reload_table = getattr(table, "reload", None)
    if callable(reload_table):
        reload_table()

    schema_obj = getattr(table, "table_schema", None) or getattr(table, "schema", None)
    if schema_obj is None:
        raise RuntimeError(
            f"Could not read schema for MaxCompute table {table_name!r}."
        )

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

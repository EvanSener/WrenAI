"""Small SQL formatting helpers shared by advanced graph renderers."""

from __future__ import annotations

from sqlglot import exp


def select_sql(
    select_items: list[str],
    from_sql: str,
    joins: list[str],
    group_items: list[str],
) -> str:
    lines = ["SELECT"]
    lines.extend(
        f"  {item}{',' if index < len(select_items) - 1 else ''}"
        for index, item in enumerate(select_items)
    )
    lines.append(from_sql)
    lines.extend(joins)
    if group_items:
        lines.append("GROUP BY")
        lines.extend(
            f"  {item}{',' if index < len(group_items) - 1 else ''}"
            for index, item in enumerate(group_items)
        )
    return "\n".join(lines)


def column_sql(alias: str, field: str, dialect: str | None) -> str:
    return exp.column(field, table=alias).sql(dialect=dialect)

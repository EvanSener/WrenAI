"""Safe SQL expression parsing shared by dynamic graph calculations."""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from wren.semantic_graph.model import GraphPlanningError

_STATEMENT_KEYS = {
    "alter",
    "block",
    "cache",
    "command",
    "commit",
    "copy",
    "create",
    "delete",
    "drop",
    "execute",
    "except",
    "grant",
    "insert",
    "intersect",
    "loaddata",
    "merge",
    "pragma",
    "revoke",
    "rollback",
    "select",
    "set",
    "show",
    "transaction",
    "truncatetable",
    "uncache",
    "union",
    "update",
    "use",
}


def parse_calculation_expression(
    expression: str,
    *,
    dialect: str | None,
    name: str,
) -> exp.Expression:
    """Parse exactly one scalar expression and reject SQL statements/subqueries."""

    try:
        parsed_items = sqlglot.parse(expression, dialect=dialect)
    except (ParseError, ValueError) as exc:
        raise GraphPlanningError(
            "GRAPH_CALCULATION_EXPRESSION_INVALID",
            f"cannot parse calculation '{name}': {exc}",
        ) from exc
    if len(parsed_items) != 1 or parsed_items[0] is None:
        raise GraphPlanningError(
            "GRAPH_CALCULATION_QUERY_FORBIDDEN",
            f"calculation '{name}' must contain exactly one expression",
        )
    parsed = parsed_items[0]
    forbidden = sorted(
        {
            node.key
            for node in parsed.walk()
            if node.key in _STATEMENT_KEYS
            or isinstance(
                node,
                (exp.Block, exp.Command, exp.DDL, exp.DML, exp.Query, exp.Subquery),
            )
        }
    )
    if forbidden:
        raise GraphPlanningError(
            "GRAPH_CALCULATION_QUERY_FORBIDDEN",
            f"calculation '{name}' cannot contain SQL statements or subqueries",
            details={"forbiddenNodes": forbidden},
        )
    return parsed

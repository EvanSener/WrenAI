"""Tokenizer and parser for the constrained graph inspection language."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from wren.semantic_graph.inspection_tokens import (
    GraphInspectionError,
    _Parameter,
    _reject_forbidden_words,
    _Token,
    _tokenize,
)


@dataclass(frozen=True)
class _PropertyRef:
    variable: str
    path: tuple[str, ...]

    @property
    def text(self) -> str:
        return ".".join((self.variable, *self.path))


@dataclass(frozen=True)
class _VariableRef:
    variable: str

    @property
    def text(self) -> str:
        return self.variable


@dataclass(frozen=True)
class _NodePattern:
    variable: str
    label: str | None
    properties: tuple[tuple[str, Any], ...]


@dataclass(frozen=True)
class _EdgePattern:
    variable: str | None
    label: str | None
    properties: tuple[tuple[str, Any], ...]


@dataclass(frozen=True)
class _MatchPattern:
    left: _NodePattern
    edge: _EdgePattern | None
    right: _NodePattern | None
    direction: Literal["node", "outgoing", "incoming", "any"]


@dataclass(frozen=True)
class _Predicate:
    property: _PropertyRef
    operator: Literal["=", "CONTAINS"]
    value: Any


@dataclass(frozen=True)
class _ReturnItem:
    expression: _VariableRef | _PropertyRef | Literal["*"]
    alias: str


@dataclass(frozen=True)
class _OrderItem:
    expression: _VariableRef | _PropertyRef
    descending: bool


@dataclass(frozen=True)
class _ParsedQuery:
    pattern: _MatchPattern
    predicates: tuple[_Predicate, ...]
    returns: tuple[_ReturnItem, ...]
    order_by: tuple[_OrderItem, ...]
    limit: int | _Parameter | None


def parse_inspection_query(
    query: str,
    parameters: Mapping[str, Any],
    *,
    max_rows: int,
) -> _ParsedQuery:
    """Perform lexical safety checks, parse the query, and bind parameters."""

    tokens = _tokenize(query)
    _reject_forbidden_words(tokens)
    parsed = _Parser(tokens).parse()
    return _bind_parameters(parsed, parameters, max_rows=max_rows)


class _Parser:
    def __init__(self, tokens: list[_Token]) -> None:
        self.tokens = tokens
        self.index = 0

    def parse(self) -> _ParsedQuery:
        self._expect_word("MATCH")
        left = self._parse_node()
        edge: _EdgePattern | None = None
        right: _NodePattern | None = None
        direction: Literal["node", "outgoing", "incoming", "any"] = "node"

        if self._accept("DASH"):
            edge = (
                self._parse_edge()
                if self._at("LBRACK")
                else _EdgePattern(None, None, ())
            )
            if self._accept("ARROW_RIGHT"):
                direction = "outgoing"
            elif self._accept("DASH"):
                direction = "any"
            else:
                self._syntax("expected '->' or '-' after relationship pattern")
            right = self._parse_node()
        elif self._accept("ARROW_LEFT"):
            edge = (
                self._parse_edge()
                if self._at("LBRACK")
                else _EdgePattern(None, None, ())
            )
            self._expect("DASH", "expected '-' after relationship pattern")
            direction = "incoming"
            right = self._parse_node()

        variables = [left.variable]
        if edge is not None and edge.variable is not None:
            variables.append(edge.variable)
        if right is not None:
            variables.append(right.variable)
        duplicates = sorted(
            variable for variable in set(variables) if variables.count(variable) > 1
        )
        if duplicates:
            self._syntax("pattern variables must be unique: " + ", ".join(duplicates))

        predicates: list[_Predicate] = []
        if self._accept_word("WHERE"):
            predicates.append(self._parse_predicate())
            while self._accept_word("AND"):
                predicates.append(self._parse_predicate())

        self._expect_word("RETURN")
        returns = [self._parse_return_item()]
        while self._accept("COMMA"):
            returns.append(self._parse_return_item())
        if len(returns) > 1 and any(item.expression == "*" for item in returns):
            self._syntax("RETURN * cannot be combined with other expressions")

        aliases = [item.alias for item in returns]
        duplicate_aliases = sorted(
            alias for alias in set(aliases) if aliases.count(alias) > 1
        )
        if duplicate_aliases:
            self._syntax(
                "RETURN aliases must be unique: " + ", ".join(duplicate_aliases)
            )

        order_by: list[_OrderItem] = []
        if self._accept_word("ORDER"):
            self._expect_word("BY")
            order_by.append(self._parse_order_item())
            while self._accept("COMMA"):
                order_by.append(self._parse_order_item())

        limit: int | _Parameter | None = None
        if self._accept_word("LIMIT"):
            token = self._peek()
            if token.kind == "PARAM":
                self.index += 1
                limit = _Parameter(token.value, token.position)
            elif token.kind == "NUMBER" and isinstance(token.value, int):
                self.index += 1
                limit = token.value
            else:
                self._syntax("LIMIT requires an integer or parameter", token)

        self._expect("EOF", "unexpected input after query")
        available = set(variables)
        self._validate_references(predicates, returns, order_by, available)
        return _ParsedQuery(
            pattern=_MatchPattern(left, edge, right, direction),
            predicates=tuple(predicates),
            returns=tuple(returns),
            order_by=tuple(order_by),
            limit=limit,
        )

    def _parse_node(self) -> _NodePattern:
        self._expect("LPAREN", "expected '('")
        variable = self._expect_identifier("node variable")
        label = None
        if self._accept("COLON"):
            label = self._expect_identifier("node label")
        properties = self._parse_property_map() if self._at("LBRACE") else ()
        self._expect("RPAREN", "expected ')' after node pattern")
        return _NodePattern(variable, label, properties)

    def _parse_edge(self) -> _EdgePattern:
        self._expect("LBRACK", "expected '['")
        variable = None
        label = None
        if self._at("IDENT"):
            variable = self._expect_identifier("relationship variable")
        if self._accept("COLON"):
            label = self._expect_identifier("relationship type")
        properties = self._parse_property_map() if self._at("LBRACE") else ()
        self._expect("RBRACK", "expected ']' after relationship pattern")
        return _EdgePattern(variable, label, properties)

    def _parse_property_map(self) -> tuple[tuple[str, Any], ...]:
        self._expect("LBRACE", "expected '{'")
        values: list[tuple[str, Any]] = []
        if not self._at("RBRACE"):
            while True:
                name = self._expect_identifier("property name")
                self._expect("COLON", "expected ':' after property name")
                values.append((name, self._parse_value()))
                if not self._accept("COMMA"):
                    break
        self._expect("RBRACE", "expected '}' after properties")
        names = [name for name, _ in values]
        if len(names) != len(set(names)):
            self._syntax("property map contains duplicate keys")
        return tuple(values)

    def _parse_predicate(self) -> _Predicate:
        reference = self._parse_property_reference(require_property=True)
        if self._accept("EQ"):
            operator: Literal["=", "CONTAINS"] = "="
        elif self._accept_word("CONTAINS"):
            operator = "CONTAINS"
        else:
            self._syntax("WHERE supports only '=' and CONTAINS")
        return _Predicate(reference, operator, self._parse_value())

    def _parse_return_item(self) -> _ReturnItem:
        if self._accept("STAR"):
            expression: _VariableRef | _PropertyRef | Literal["*"] = "*"
            default_alias = "*"
        else:
            expression = self._parse_reference()
            default_alias = expression.text
        has_alias = self._accept_word("AS")
        if expression == "*" and has_alias:
            self._syntax("RETURN * cannot have an alias")
        alias = self._expect_identifier("RETURN alias") if has_alias else default_alias
        return _ReturnItem(expression, alias)

    def _parse_order_item(self) -> _OrderItem:
        expression = self._parse_reference()
        descending = False
        if self._accept_word("ASC"):
            descending = False
        elif self._accept_word("DESC"):
            descending = True
        return _OrderItem(expression, descending)

    def _parse_reference(self) -> _VariableRef | _PropertyRef:
        variable = self._expect_identifier("variable or RETURN alias")
        if not self._accept("DOT"):
            return _VariableRef(variable)
        path = [self._expect_identifier("property name")]
        while self._accept("DOT"):
            path.append(self._expect_identifier("property name"))
        return _PropertyRef(variable, tuple(path))

    def _parse_property_reference(self, *, require_property: bool) -> _PropertyRef:
        reference = self._parse_reference()
        if require_property and isinstance(reference, _VariableRef):
            self._syntax("WHERE requires a variable property, for example n.name")
        assert isinstance(reference, _PropertyRef)
        return reference

    def _parse_value(self) -> Any:
        negative = self._accept("DASH")
        token = self._peek()
        if token.kind == "PARAM":
            if negative:
                self._syntax("parameters cannot be prefixed with '-'", token)
            self.index += 1
            return _Parameter(token.value, token.position)
        if token.kind in {"STRING", "NUMBER"}:
            self.index += 1
            if negative:
                if token.kind != "NUMBER":
                    self._syntax("only numeric literals may be negative", token)
                return -token.value
            return token.value
        if token.kind == "IDENT" and token.value.upper() in {
            "TRUE",
            "FALSE",
            "NULL",
        }:
            if negative:
                self._syntax("only numeric literals may be negative", token)
            self.index += 1
            return {"TRUE": True, "FALSE": False, "NULL": None}[token.value.upper()]
        self._syntax("expected a string, number, boolean, null, or parameter", token)

    def _validate_references(
        self,
        predicates: Sequence[_Predicate],
        returns: Sequence[_ReturnItem],
        order_by: Sequence[_OrderItem],
        variables: set[str],
    ) -> None:
        return_aliases = {item.alias for item in returns}
        for predicate in predicates:
            self._require_variable(predicate.property.variable, variables)
        for item in returns:
            expression = item.expression
            if expression == "*":
                continue
            self._require_variable(expression.variable, variables)
        for item in order_by:
            expression = item.expression
            if (
                isinstance(expression, _VariableRef)
                and expression.variable in return_aliases
            ):
                continue
            self._require_variable(expression.variable, variables)

    def _require_variable(self, variable: str, variables: set[str]) -> None:
        if variable not in variables:
            self._syntax(f"unknown pattern variable '{variable}'")

    def _at(self, kind: str) -> bool:
        return self._peek().kind == kind

    def _peek(self) -> _Token:
        return self.tokens[self.index]

    def _accept(self, kind: str) -> bool:
        if self._at(kind):
            self.index += 1
            return True
        return False

    def _accept_word(self, word: str) -> bool:
        token = self._peek()
        if token.kind == "IDENT" and not token.quoted and token.value.upper() == word:
            self.index += 1
            return True
        return False

    def _expect(self, kind: str, message: str) -> _Token:
        token = self._peek()
        if token.kind != kind:
            self._syntax(message, token)
        self.index += 1
        return token

    def _expect_word(self, word: str) -> None:
        if not self._accept_word(word):
            self._syntax(f"expected {word}")

    def _expect_identifier(self, description: str) -> str:
        token = self._expect("IDENT", f"expected {description}")
        return token.value

    def _syntax(self, message: str, token: _Token | None = None) -> None:
        raise GraphInspectionError(
            "QUERY_SYNTAX_ERROR",
            message,
            position=(token or self._peek()).position,
        )


def _bind_parameters(
    parsed: _ParsedQuery,
    parameters: Mapping[str, Any],
    *,
    max_rows: int,
) -> _ParsedQuery:
    if not isinstance(parameters, Mapping):
        raise GraphInspectionError("INVALID_PARAMETERS", "parameters must be a mapping")

    used: set[str] = set()

    def bind(value: Any) -> Any:
        if not isinstance(value, _Parameter):
            return value
        used.add(value.name)
        if value.name not in parameters:
            raise GraphInspectionError(
                "PARAMETER_MISSING",
                f"missing parameter '${value.name}'",
                position=value.position,
            )
        parameter = parameters[value.name]
        try:
            json.dumps(parameter, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise GraphInspectionError(
                "INVALID_PARAMETER",
                f"parameter '${value.name}' must be JSON-compatible",
                position=value.position,
            ) from exc
        return parameter

    def bind_node(node: _NodePattern) -> _NodePattern:
        return _NodePattern(
            node.variable,
            node.label,
            tuple((name, bind(value)) for name, value in node.properties),
        )

    pattern = parsed.pattern
    edge = pattern.edge
    bound_edge = None
    if edge is not None:
        bound_edge = _EdgePattern(
            edge.variable,
            edge.label,
            tuple((name, bind(value)) for name, value in edge.properties),
        )
    bound_pattern = _MatchPattern(
        bind_node(pattern.left),
        bound_edge,
        bind_node(pattern.right) if pattern.right is not None else None,
        pattern.direction,
    )
    predicates = tuple(
        _Predicate(predicate.property, predicate.operator, bind(predicate.value))
        for predicate in parsed.predicates
    )
    limit_value = bind(parsed.limit)
    if limit_value is not None:
        if isinstance(limit_value, bool) or not isinstance(limit_value, int):
            raise GraphInspectionError(
                "INVALID_LIMIT", "LIMIT must resolve to an integer"
            )
        if limit_value < 0 or limit_value > max_rows:
            raise GraphInspectionError(
                "INVALID_LIMIT", f"LIMIT must be between 0 and {max_rows}"
            )

    unknown = sorted(set(parameters) - used)
    if unknown:
        raise GraphInspectionError(
            "UNUSED_PARAMETERS",
            "unused parameters: " + ", ".join(f"${name}" for name in unknown),
            details={"parameters": unknown},
        )
    return _ParsedQuery(
        bound_pattern,
        predicates,
        parsed.returns,
        parsed.order_by,
        limit_value,
    )


__all__ = ["GraphInspectionError", "parse_inspection_query"]

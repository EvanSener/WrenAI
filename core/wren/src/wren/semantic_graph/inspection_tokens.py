"""Lexical safety checks for the constrained graph inspection language."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

_FORBIDDEN_WORDS = frozenset(
    {
        "ALTER",
        "CALL",
        "CREATE",
        "DELETE",
        "DENY",
        "DETACH",
        "DROP",
        "EXEC",
        "EXECUTE",
        "FOREACH",
        "GRANT",
        "LOAD",
        "MERGE",
        "REMOVE",
        "REVOKE",
        "SET",
        "START",
        "STOP",
        "TRANSACTION",
        "UNION",
        "UNWIND",
        "USE",
        "YIELD",
    }
)
_MAX_QUERY_LENGTH = 10_000


class GraphInspectionError(ValueError):
    """A structured parse, safety, parameter, or graph validation error."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        position: int | None = None,
        details: Any = None,
    ) -> None:
        self.code = code
        self.position = position
        self.details = details
        suffix = f" at character {position}" if position is not None else ""
        super().__init__(f"{message}{suffix}")

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"code": self.code, "message": str(self)}
        if self.position is not None:
            value["position"] = self.position
        if self.details is not None:
            value["details"] = self.details
        return value


@dataclass(frozen=True)
class _Token:
    kind: str
    value: Any
    position: int
    quoted: bool = False


@dataclass(frozen=True)
class _Parameter:
    name: str
    position: int


def _tokenize(query: str) -> list[_Token]:
    if not isinstance(query, str) or not query.strip():
        raise GraphInspectionError("QUERY_EMPTY", "query must be a non-empty string")
    if len(query) > _MAX_QUERY_LENGTH:
        raise GraphInspectionError(
            "QUERY_TOO_LARGE", f"query exceeds {_MAX_QUERY_LENGTH} characters"
        )

    tokens: list[_Token] = []
    index = 0
    punctuation = {
        "(": "LPAREN",
        ")": "RPAREN",
        "[": "LBRACK",
        "]": "RBRACK",
        "{": "LBRACE",
        "}": "RBRACE",
        ":": "COLON",
        ",": "COMMA",
        ".": "DOT",
        "=": "EQ",
        "*": "STAR",
    }
    while index < len(query):
        char = query[index]
        if char.isspace():
            index += 1
            continue
        if (
            query.startswith("/*", index)
            or query.startswith("//", index)
            or query.startswith("--", index)
        ):
            raise GraphInspectionError(
                "QUERY_COMMENTS_FORBIDDEN",
                "comments are not allowed in inspection queries",
                position=index,
            )
        if char == ";":
            raise GraphInspectionError(
                "MULTIPLE_STATEMENTS_FORBIDDEN",
                "semicolons and multiple statements are not allowed",
                position=index,
            )
        if query.startswith("->", index):
            tokens.append(_Token("ARROW_RIGHT", "->", index))
            index += 2
            continue
        if query.startswith("<-", index):
            tokens.append(_Token("ARROW_LEFT", "<-", index))
            index += 2
            continue
        if char == "-":
            tokens.append(_Token("DASH", char, index))
            index += 1
            continue
        if char in punctuation:
            tokens.append(_Token(punctuation[char], char, index))
            index += 1
            continue
        if char in {"'", '"'}:
            start = index
            value, index = _read_string(query, index)
            tokens.append(_Token("STRING", value, start))
            continue
        if char == "`":
            value, start, index = _read_quoted_identifier(query, index)
            tokens.append(_Token("IDENT", value, start, quoted=True))
            continue
        if char == "$":
            start = index
            index += 1
            value, index = _read_identifier(query, index)
            if not value:
                raise GraphInspectionError(
                    "QUERY_SYNTAX_ERROR",
                    "parameter requires a name",
                    position=start,
                )
            tokens.append(_Token("PARAM", value, start))
            continue
        if char.isdigit():
            token, index = _read_number(query, index)
            tokens.append(token)
            continue
        if char == "_" or char.isalpha():
            start = index
            value, index = _read_identifier(query, index)
            tokens.append(_Token("IDENT", value, start))
            continue
        raise GraphInspectionError(
            "QUERY_SYNTAX_ERROR",
            f"unsupported character {char!r}",
            position=index,
        )
    tokens.append(_Token("EOF", None, len(query)))
    return tokens


def _read_identifier(query: str, index: int) -> tuple[str, int]:
    start = index
    if index >= len(query) or not (query[index] == "_" or query[index].isalpha()):
        return "", index
    index += 1
    while index < len(query) and (query[index] == "_" or query[index].isalnum()):
        index += 1
    return query[start:index], index


def _read_quoted_identifier(query: str, index: int) -> tuple[str, int, int]:
    start = index
    index += 1
    result: list[str] = []
    while index < len(query):
        if query[index] == "`":
            if index + 1 < len(query) and query[index + 1] == "`":
                result.append("`")
                index += 2
                continue
            if not result:
                raise GraphInspectionError(
                    "QUERY_SYNTAX_ERROR",
                    "quoted identifier cannot be empty",
                    position=start,
                )
            return "".join(result), start, index + 1
        result.append(query[index])
        index += 1
    raise GraphInspectionError(
        "QUERY_SYNTAX_ERROR",
        "unterminated quoted identifier",
        position=start,
    )


def _read_string(query: str, index: int) -> tuple[str, int]:
    quote = query[index]
    start = index
    index += 1
    result: list[str] = []
    escapes = {
        "\\": "\\",
        "'": "'",
        '"': '"',
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
    }
    while index < len(query):
        char = query[index]
        if char == quote:
            return "".join(result), index + 1
        if char != "\\":
            result.append(char)
            index += 1
            continue
        escape_position = index
        index += 1
        if index >= len(query):
            break
        escaped = query[index]
        if escaped == "u":
            digits = query[index + 1 : index + 5]
            if len(digits) != 4 or any(
                char not in "0123456789abcdefABCDEF" for char in digits
            ):
                raise GraphInspectionError(
                    "QUERY_SYNTAX_ERROR",
                    "invalid unicode escape",
                    position=escape_position,
                )
            result.append(chr(int(digits, 16)))
            index += 5
            continue
        if escaped not in escapes:
            raise GraphInspectionError(
                "QUERY_SYNTAX_ERROR",
                f"unsupported string escape \\{escaped}",
                position=escape_position,
            )
        result.append(escapes[escaped])
        index += 1
    raise GraphInspectionError(
        "QUERY_SYNTAX_ERROR", "unterminated string", position=start
    )


def _read_number(query: str, index: int) -> tuple[_Token, int]:
    start = index
    while index < len(query) and query[index].isdigit():
        index += 1
    is_float = False
    if index < len(query) and query[index] == ".":
        is_float = True
        index += 1
        decimal_start = index
        while index < len(query) and query[index].isdigit():
            index += 1
        if decimal_start == index:
            raise GraphInspectionError(
                "QUERY_SYNTAX_ERROR",
                "numeric literal requires digits after the decimal point",
                position=start,
            )
    raw = query[start:index]
    return _Token("NUMBER", float(raw) if is_float else int(raw), start), index


def _reject_forbidden_words(tokens: Sequence[_Token]) -> None:
    previous: _Token | None = None
    for token in tokens:
        if (
            token.kind == "IDENT"
            and not token.quoted
            and token.value.upper() in _FORBIDDEN_WORDS
            and (previous is None or previous.kind != "DOT")
        ):
            raise GraphInspectionError(
                "READ_ONLY_VIOLATION",
                f"{token.value.upper()} is not allowed in read-only graph inspection",
                position=token.position,
            )
        previous = token


__all__ = ["GraphInspectionError"]

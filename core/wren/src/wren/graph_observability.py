"""Shared CLI error rendering and Graph query timing helpers.

The helpers in this module deliberately keep machine-readable query output on
stdout and write diagnostics to stderr.  ``echo_cli_error`` is intentionally
generic enough for the root query/dry-run commands to reuse later.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from typing import Any, Iterator

import typer

from wren.model.error import WrenError

_MAX_COMPACT_ERROR_LENGTH = 240


def compact_exception_summary(
    exc: BaseException,
    *,
    max_length: int = _MAX_COMPACT_ERROR_LENGTH,
) -> str:
    """Return one bounded, non-empty line suitable for normal CLI failures."""

    message = exc.message if isinstance(exc, WrenError) else str(exc)
    summary = next(
        (line.strip() for line in message.splitlines() if line.strip()),
        "unexpected error",
    )
    if len(summary) <= max_length:
        return summary
    return f"{summary[: max_length - 3].rstrip()}..."


def _enum_name(value: Any) -> str | None:
    name = getattr(value, "name", None)
    return str(name) if name else None


def cli_error_code(exc: BaseException) -> str | None:
    """Return a stable public error code when one is available."""

    if isinstance(exc, typer.Exit):
        return None
    if isinstance(exc, WrenError):
        return _enum_name(exc.error_code) or str(exc.error_code)
    code = getattr(exc, "code", None)
    return str(code) if code else "UNEXPECTED_ERROR"


def format_cli_error(exc: BaseException, *, verbose: bool = False) -> list[str]:
    """Format a compact CLI error, optionally followed by full diagnostics.

    Graph planning errors are recognized by their stable ``code``/``details``
    attributes so this module does not need to import the semantic graph model.
    """

    code = cli_error_code(exc) or "UNEXPECTED_ERROR"
    phase: str | None = None
    if isinstance(exc, WrenError):
        phase = _enum_name(exc.phase)

    phase_text = f" phase={phase}" if phase else ""
    lines = [f"Error [{code}]: {compact_exception_summary(exc)}{phase_text}"]
    if not verbose:
        return lines

    full_message = str(exc)
    if full_message and full_message != compact_exception_summary(exc):
        lines.append(f"Details: {full_message}")

    details = getattr(exc, "details", None)
    if details is not None:
        lines.append(json.dumps(details, indent=2, ensure_ascii=False, default=str))
    elif isinstance(exc, WrenError) and exc.metadata is not None:
        lines.append(
            json.dumps(exc.metadata, indent=2, ensure_ascii=False, default=str)
        )
    return lines


def echo_cli_error(exc: BaseException, *, verbose: bool = False) -> None:
    """Write a formatted error to stderr without contaminating stdout."""

    for line in format_cli_error(exc, verbose=verbose):
        typer.echo(line, err=True)


class GraphQueryTimings:
    """Collect monotonic Graph query stage timings and emit one JSON line."""

    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled
        self._started_ns = time.perf_counter_ns()
        self._stages_ms: dict[str, float] = {}
        self.failed_stage: str | None = None
        self.error_code: str | None = None

    def record_error(
        self,
        exc: BaseException,
        *,
        stage: str | None = None,
    ) -> None:
        """Record failure metadata without exposing exception implementation types."""

        if not self.enabled:
            return
        if self.failed_stage is None and stage is not None:
            self.failed_stage = stage
        if self.error_code is None:
            self.error_code = cli_error_code(exc)

    @contextmanager
    def measure(self, name: str) -> Iterator[None]:
        if not self.enabled:
            yield
            return

        started_ns = time.perf_counter_ns()
        try:
            yield
        except BaseException as exc:
            self.record_error(exc, stage=name)
            raise
        finally:
            duration_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
            self._stages_ms[name] = round(
                self._stages_ms.get(name, 0.0) + duration_ms,
                3,
            )

    def emit(self, *, status: str) -> None:
        if not self.enabled:
            return
        total_ms = round(
            (time.perf_counter_ns() - self._started_ns) / 1_000_000,
            3,
        )
        payload: dict[str, Any] = {
            "schemaVersion": 1,
            "kind": "GRAPH_QUERY_TIMINGS",
            "status": status,
            "totalMs": total_ms,
            "stagesMs": self._stages_ms,
            "overheadMs": round(
                max(0.0, total_ms - sum(self._stages_ms.values())),
                3,
            ),
        }
        if self.failed_stage is not None:
            payload["failedStage"] = self.failed_stage
        if self.error_code is not None:
            payload["errorCode"] = self.error_code
        typer.echo(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            err=True,
        )

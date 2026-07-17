"""Prompt-shaping helpers for ``wren ask``.

``wren ask`` wraps a user's natural-language prompt in one of two bundled
templates and prints the rendered result to stdout. It does not execute any
query — it produces a prompt for an agent to consume.

Modes:
- ``guided`` — prepends a strict task flow (for weaker LLMs).
- ``direct`` — minimal wrapping (for stronger LLMs).
"""

from __future__ import annotations

import json
from importlib import resources

_TEMPLATES_DIR = "ask_templates"
_USER_PROMPT_PLACEHOLDER = "<USER_PROMPT_JSON>"

MODES = ("guided", "direct")


class UnknownAskModeError(ValueError):
    """Raised when a mode other than ``guided`` / ``direct`` is requested."""


def render(mode: str, user_prompt: str) -> str:
    """Render ``user_prompt`` as JSON data inside the trusted template."""
    if mode not in MODES:
        raise UnknownAskModeError(mode)
    tpl = (resources.files("wren") / _TEMPLATES_DIR / f"{mode}.md.tmpl").read_text(
        encoding="utf-8"
    )
    user_data = json.dumps({"user_question": user_prompt}, ensure_ascii=False)
    return tpl.replace(_USER_PROMPT_PLACEHOLDER, user_data)

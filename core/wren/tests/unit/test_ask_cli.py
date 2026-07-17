"""Tests for `wren ask` prompt shaping."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wren import ask as ask_mod
from wren.cli import app

pytestmark = pytest.mark.unit

runner = CliRunner()


def test_no_mode_flag_rejected():
    result = runner.invoke(app, ["ask", "show me revenue"])
    assert result.exit_code != 0
    out = result.output + (result.stderr if result.stderr_bytes else "")
    assert "--guided" in out and "--direct" in out


def test_both_mode_flags_rejected():
    result = runner.invoke(app, ["ask", "show me revenue", "--guided", "--direct"])
    assert result.exit_code != 0


def test_guided_includes_task_flow_and_substitutes_prompt():
    result = runner.invoke(app, ["ask", "top 5 customers by revenue", "--guided"])
    assert result.exit_code == 0
    assert "TASK TYPE A" in result.output
    assert "TASK TYPE B" in result.output
    assert "1–3 Wren commands" in result.output
    assert "wren context instructions --compact" in result.output
    assert "wren graph query" in result.output
    assert "--execute" in result.output
    assert "--result-output json" in result.output
    assert "wren cube resolve" in result.output
    assert "wren cube query" in result.output
    assert "first Wren Memory failure" in result.output
    assert "Never store unless the user explicitly asks" in result.output
    assert "hard ceiling of 2 attempts" in result.output
    assert "Never run a third SQL" in result.output
    assert "omitted from the graph plan" in result.output
    assert "omitted from graph explain" not in result.output
    assert "top 5 customers by revenue" in result.output
    assert "<USER_PROMPT>" not in result.output  # placeholder substituted


def test_direct_minimal_and_substitutes_prompt():
    result = runner.invoke(app, ["ask", "monthly orders trend", "--direct"])
    assert result.exit_code == 0
    assert "wren skills list" in result.output
    assert "wren --help" in result.output
    assert "Do not run either command before an ordinary data question" in result.output
    assert "1–3 Wren commands" in result.output
    assert "wren context instructions --compact" in result.output
    assert "--execute" in result.output
    assert "do not call Memory again" in result.output
    assert "at most two candidate" in result.output
    assert "monthly orders trend" in result.output
    assert "<UNTRUSTED_USER_INPUT_JSON>" in result.output
    assert '"user_question": "monthly orders trend"' in result.output
    assert "<USER_PROMPT>" not in result.output
    # direct mode should NOT include the guided TASK TYPE structure
    assert "TASK TYPE A" not in result.output


def test_render_api_known_modes():
    for mode in ask_mod.MODES:
        out = ask_mod.render(mode, "hello world")
        assert "hello world" in out
        assert "<USER_PROMPT>" not in out


def test_render_unknown_mode_raises():
    with pytest.raises(ask_mod.UnknownAskModeError):
        ask_mod.render("auto", "anything")


def test_user_prompt_with_template_placeholder_substring_is_safe():
    # Prompt containing the literal placeholder shouldn't break rendering;
    # we only do one replacement of the bundled-template placeholder.
    prompt = "Show literal <USER_PROMPT> usage examples"
    out = ask_mod.render("direct", prompt)
    # the bundled placeholder is gone and the prompt is present (verbatim)
    assert prompt in out


def test_render_json_escapes_prompt_line_breaks():
    out = ask_mod.render("direct", "revenue\nsecond line")
    data_block = out.split("<UNTRUSTED_USER_INPUT_JSON>", 1)[1].split(
        "</UNTRUSTED_USER_INPUT_JSON>", 1
    )[0]
    assert "revenue\\nsecond line" in data_block
    assert "revenue\nsecond line" not in data_block


def test_ordinary_query_contract_is_consistent_across_agent_surfaces():
    repo = Path(__file__).resolve().parents[4]
    surfaces = {
        "discovery skill": repo / "skills" / "wren" / "SKILL.md",
        "packaged usage": (
            repo
            / "core"
            / "wren"
            / "src"
            / "wren"
            / "skills_content"
            / "usage"
            / "SKILL.md"
        ),
        "direct ask": (
            repo / "core" / "wren" / "src" / "wren" / "ask_templates" / "direct.md.tmpl"
        ),
        "guided ask": (
            repo / "core" / "wren" / "src" / "wren" / "ask_templates" / "guided.md.tmpl"
        ),
    }

    for name, path in surfaces.items():
        content = path.read_text(encoding="utf-8")
        normalized = content.replace("*", "").lower()
        assert "wren context instructions --compact" in content, name
        assert "wren graph query" in content and "--execute" in content, name
        assert "2–4 Wren commands" not in content, name
        assert re.search(r"(?:two|2).{0,50}(?:attempt|candidate)", normalized), name

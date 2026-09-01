"""Agent Builder must be able to PLAN and to VERIFY its own work.

It is the same mechanism as any coding agent — the model returns data, the orchestrator calls
tools, repeat. What made its output worse was not capability but the toolbox: phase 5 wrote its
allow-list as "no shell, no browser, no web: building an agent is a filesystem job", which
removed exactly the abilities that turn writing-and-hoping into writing-checking-fixing.

The cost was concrete. A generated agent (`inbox-triage`, built by gemini-3.1-pro-preview with
zero fallbacks — a capable model, undegraded) shipped a UI whose every event branch was dead,
because nothing could run it and nothing checked it.

These tests pin the fix so it cannot be quietly optimised away again.
"""

import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "agents" / "agent-builder"


def _allow() -> set[str]:
    data = tomllib.loads((AGENT / "agent.toml").read_text(encoding="utf-8"))
    return set((data.get("tools") or {}).get("allow") or [])


def _agents_md() -> str:
    return (AGENT / "AGENTS.md").read_text(encoding="utf-8")


# --- C1: the tools ----------------------------------------------------------
@pytest.mark.parametrize(
    ("tool", "why"),
    [
        ("update_plan", "keeps a live checklist the user can watch"),
        ("exec", "the only way it can RUN what it wrote"),
        ("verify_answer", "catches an answer that only promises instead of delivering"),
        ("web_search", "the only way it can READ the service it is about to integrate"),
        ("web_fetch", "reads the API/MCP docs it found, instead of recalling them"),
    ],
)
def test_it_can_plan_and_verify(tool, why):
    assert tool in _allow(), f"agent-builder lost `{tool}` — {why}"


def test_it_still_cannot_reach_the_whole_catalog():
    """Restoring planning and verification is not an excuse to grant everything. The point was
    never least privilege for its own sake, it was the wrong CHOICE of privileges.

    `web_search` USED TO BE ON THIS LIST, and that was the same mistake one layer up. This
    file's own opening names "no shell, no browser, no web" as the bad allow-list, then kept
    the "no web" half — so the agent could run what it wrote but could not read what it was
    writing AGAINST. Asked for an AWS cost monitor it had no way to look up Cost Explorer or
    check which MCP server exposes cost tools, so it GUESSED a package name, and the guess did
    not expose the tools the agent needed. That is writing-and-hoping about somebody else's API
    instead of about a UI event branch.

    `browser` and `computer_use` stay out: reading documentation is not driving a desktop.
    """
    allow = _allow()
    for never in ("browser", "computer_use", "create_webhook"):
        assert never not in allow, f"agent-builder does not need `{never}`"


def test_the_authoring_plugin_actually_imports():
    """The entry point that registers create_agent / validate_agent / reload_agent /
    package_agent / scaffold_ui. If it raises, ALL FIVE disappear and the only symptom is
    `RuntimeError: tool not available: validate_agent` at the moment someone tries to use one.

    Nothing covered this: every other test imports `agent_authoring.domain.*` directly, so a
    stray indent in the plugin module itself passed a green suite and broke the agent. One
    import is the whole check.
    """
    import importlib

    sys.path.insert(0, str(AGENT / "plugins" / "agent-authoring"))
    module = importlib.import_module("agent_authoring_plugin")
    assert callable(getattr(module, "register", None)), "the plugin exposes no register()"


def test_its_own_tools_are_not_named_in_the_allow_list():
    """Private tools are implicitly allowed; naming them here would imply `main` could too."""
    allow = _allow()
    for private in ("create_agent", "validate_agent", "reload_agent", "package_agent"):
        assert private not in allow


# --- C3: the instructions that make it use them -----------------------------
# Tools without instructions get ignored, so the rules are part of the same change.
def test_it_is_told_to_plan_first():
    assert "update_plan" in _agents_md()


def test_it_is_told_to_read_rather_than_guess():
    md = _agents_md()
    assert "guess" in md.lower()
    assert "vendor/agentd-client.js" in md, "point it at the SDK it actually ships with"


def test_it_is_only_pointed_at_files_that_SHIP():
    """A user's install contains `main/skills` and `agent-builder` — nothing else (see
    scripts/hatch_build.py). Telling it to read `agents/weather/ui/app.js` or
    `clients/sdk-js/...` sends it to a file that is not there, it gets "not found", and it
    guesses anyway — the exact behaviour these rules exist to prevent."""
    md = _agents_md()
    for absent in ("agents/weather/", "clients/sdk-js", "figure-creator", "expense-summarizer"):
        assert absent not in md, f"points at {absent}, which does not exist on a real install"


def test_it_is_told_NOT_to_copy_other_agents():
    md = _agents_md().lower()
    assert "do not go looking at other agents" in md


def test_it_is_told_to_run_what_it_writes():
    md = _agents_md()
    assert "node --check" in md, "generated JS must be syntax-checked before hand-off"
    assert "exec" in md


def test_it_is_told_that_promising_is_not_doing():
    """The prevention rule: a standing instruction present on EVERY turn, rather than relying
    on the runtime's retry net — which was itself found broken."""
    md = _agents_md().lower()
    assert "announcing an action you have not taken" in md or "only promises" in md


def test_finished_means_verified():
    md = _agents_md().lower()
    assert "validate_agent" in md
    assert "not when the files exist" in md or "finished means verified" in md

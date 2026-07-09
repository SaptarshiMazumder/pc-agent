"""The Google ## accounts prompt block now lives in the google PLUGIN (plugins/google/), not
the core prompt builder. These are the old test_capabilities Google tests, moved to test the
plugin's `google_section` directly — and one integration check that build_system_prompt emits
it via plugin_sections."""

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "plugins" / "google"))

from google_plugin import google_section, register  # noqa: E402


def _tool(name):
    return SimpleNamespace(name=name, description=name)


def _agent(**over):
    return SimpleNamespace(name="A", id="a", workspace=Path("."), instructions="", **over)


def _cfg(**over):
    return SimpleNamespace(agent_name="A", workspace=Path("."), agent_id="main", **over)


# ── the section function (the moved block) ───────────────────────────────────


def test_section_shown_when_account_set():
    s = google_section([_tool("read")], _agent(google_account="x@y.com"), _cfg())
    assert "## Google accounts" in s and "x@y.com" in s and "user_google_email" in s


def test_section_empty_when_no_google_and_no_account():
    assert google_section([_tool("read")], _agent(), _cfg()) == ""


def test_general_guidance_when_google_tool_present_no_account():
    s = google_section([_tool("google__gmail_send")], _agent(), _cfg())
    assert "## Google accounts" in s
    assert "NOT accounts you log in as" in s  # identity-vs-recipient rule
    assert "user_google_email" in s
    assert "Default to" not in s  # nothing pinned


def test_global_default_applies():
    s = google_section([_tool("read")], _agent(), _cfg(google_account="g@x.com"))
    assert "g@x.com" in s


def test_agent_account_overrides_global():
    s = google_section(
        [_tool("read")], _agent(google_account="agent@x.com"), _cfg(google_account="global@x.com")
    )
    assert "agent@x.com" in s and "global@x.com" not in s


def test_multi_account_guidance():
    s = google_section(
        [_tool("google__gmail_send")], _agent(google_accounts=("a@x.com", "b@x.com")), _cfg()
    )
    assert "a@x.com" in s and "b@x.com" in s and "owns each resource" in s


# ── registration + integration ───────────────────────────────────────────────


def test_register_contributes_the_section():
    class _Api:
        def __init__(self):
            self.sections = []

        def register_prompt_section(self, fn):
            self.sections.append(fn)

    api = _Api()
    register(api, SimpleNamespace(config=_cfg()))
    assert api.sections == [google_section]


def test_emitted_through_build_system_prompt():
    from agentd.infrastructure.prompt import build_system_prompt

    p = build_system_prompt(
        _cfg(), [_tool("google__gmail_send")], "m", agent=_agent(), plugin_sections=[google_section]
    )
    assert "## Google accounts" in p  # the plugin's block reaches the prompt

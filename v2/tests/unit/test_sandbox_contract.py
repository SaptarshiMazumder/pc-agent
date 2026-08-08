"""A tool built by chat must be sandbox-correct BY CONSTRUCTION, not by the model remembering.

An agent's private tools are trusted on the machine that authored them and untrusted on every
machine that installs the agent. So there is a whole class of defect that is invisible where it
is written and certain where it runs — and the worst of it, a model call with no
``needs_model = True``, is one class attribute that no author has any reason to think about.

Leaving that to prose in a skill is the weakest mechanism the builder has (its own plan ranks
them: validator rule > AGENTS.md > skill). These tests pin the two mechanical ones:

  * ``create_tool`` DERIVES ``needs_model`` from the code and REFUSES the shapes that cannot
    survive installation, before the file exists.
  * ``SandboxRules`` reports the same defects in a plugin authored by hand with ``write``.

Both read the same ``domain/sandbox_contract``, so they cannot come to disagree about what
breaks under the sandbox.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from agent_authoring.domain.sandbox_contract import (
    blocking_defects,
    derive_model_need,
    undeclared_model_call,
)
from agent_authoring.domain.sandbox_rules import SandboxRules
from agent_authoring.presentation.create_tool_tool import CreateToolTool

# --- the derivation ---------------------------------------------------------


def test_a_text_model_call_is_recognised():
    code = "from agent_runtime.infrastructure.llm.oneshot import text_complete\n" "x = text_complete(model=None, prompt=p)"
    assert derive_model_need(code) == (True, "text")


def test_a_vision_call_is_recognised_as_vision():
    assert derive_model_need("out = vision_complete(model=m, prompt=p, image_paths=[f])") == (
        True,
        "vision",
    )


def test_vision_wins_when_a_tool_does_both():
    """model_kind picks the default the resolution chain uses. A vision model answers a text
    prompt; a text model cannot look at the image — so the wider capability has to win."""
    code = "a = text_complete(prompt=p)\nb = vision_complete(prompt=p, image_paths=[f])"
    assert derive_model_need(code) == (True, "vision")


def test_a_tool_that_calls_no_model_is_granted_none():
    """Not a conservative default — the correct one. Granting models to a tool with no model
    call widens what a bug in it can spend, for nothing."""
    assert derive_model_need("return ToolResult.text(str(len(params['s'])))") == (False, "text")


def test_a_mention_in_prose_is_not_a_call():
    """The pattern is anchored on the call shape, so a docstring that NAMES the function does
    not stamp the attribute. A rule that fires on comments is one people switch off."""
    assert derive_model_need("# see text_complete for the host-brokered route\nreturn 1") == (
        False,
        "text",
    )


# --- the refusals -----------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("k = os.environ['OPENAI_API_KEY']", "ENV_READ"),
        ("k = os.getenv('SOME_KEY')", "ENV_READ"),
        ("import requests", "NET_IMPORT"),
        ("from httpx import AsyncClient", "NET_IMPORT"),
    ],
)
def test_the_unambiguous_defects_block(code, expected):
    assert [c for c, _w, _f in blocking_defects(code)] == [expected]


def test_a_url_in_a_string_does_not_block():
    """Tier 2 on purpose. A refusal has to be right every time, and a docs link in a comment is
    not evidence of a network call — blocking on it would train the author to route around."""
    assert blocking_defects("# docs: https://example.com/api\nreturn ToolResult.text('ok')") == []


def test_the_host_brokered_model_route_does_not_block():
    """THE POINT of the inversion: this is the call an author SHOULD write, so it must sail
    through. If the refusal caught it there would be no legal way to call a model at all."""
    code = (
        "from agent_runtime.infrastructure.llm.oneshot import text_complete\n"
        "return ToolResult.text(text_complete(model=None, prompt=params['q']))"
    )
    assert blocking_defects(code) == []


# --- create_tool: derive + refuse, end to end -------------------------------


class _Registry:
    def __init__(self, tmp):
        self.agents_dir = str(tmp)

    def list_ids(self):
        return ["main", "note-taker"]


def _tool(tmp_path, reloaded=None):
    def _reload():
        (reloaded if reloaded is not None else []).append(True)
        return {"ok": True, "tools": [], "agentTools": {"note-taker": 1}}

    return CreateToolTool(object(), _reload, _Registry(tmp_path))


def _run(tool, params):
    return asyncio.run(tool.execute("c1", params, asyncio.Event()))


def _module(tmp_path, agent, pid):
    return (tmp_path / agent / "plugins" / pid).glob("*.py")


def test_create_tool_stamps_needs_model_without_being_asked(tmp_path):
    """The regression this whole change exists for: the model never passes needs_model — the
    parameter does not exist — so the tool has to read it off the code."""
    res = _run(
        _tool(tmp_path),
        {
            "id": "summarize",
            "agent": "note-taker",
            "name": "summarize_note",
            "code": (
                "from agent_runtime.infrastructure.llm.oneshot import text_complete\n"
                "return ToolResult.text(text_complete(model=None, prompt=params['text']))"
            ),
        },
    )
    assert not res.is_error, res.content
    src = next(_module(tmp_path, "note-taker", "summarize")).read_text(encoding="utf-8")
    assert "needs_model = True" in src
    assert "model_kind = 'text'" in src
    assert "plugin = 'summarize'" in src, "the tool must be addressable in [plugins.*] wiring"
    assert res.details["needsModel"] is True


def test_a_pure_tool_is_not_given_model_access(tmp_path):
    res = _run(
        _tool(tmp_path),
        {
            "id": "counter",
            "agent": "note-taker",
            "name": "count_words",
            "code": "return ToolResult.text(str(len(params['s'].split())))",
        },
    )
    assert not res.is_error
    src = next(_module(tmp_path, "note-taker", "counter")).read_text(encoding="utf-8")
    assert "needs_model = False" in src
    assert res.details["needsModel"] is False


def test_the_generated_module_still_compiles_with_the_new_attributes(tmp_path):
    """The template gained three formatted fields. A tool that writes a file which cannot be
    imported is worse than one that refuses."""
    _run(
        _tool(tmp_path),
        {
            "id": "vision-kit",
            "agent": "note-taker",
            "name": "describe_image",
            "code": "return ToolResult.text(vision_complete(prompt='what is this', image_paths=[params['p']]))",
        },
    )
    src = next(_module(tmp_path, "note-taker", "vision-kit")).read_text(encoding="utf-8")
    compile(src, "<generated>", "exec")
    assert "model_kind = 'vision'" in src


def test_an_agent_scoped_tool_that_reads_a_key_is_refused(tmp_path):
    res = _run(
        _tool(tmp_path),
        {
            "id": "weather",
            "agent": "note-taker",
            "name": "get_weather",
            "code": "import os\nkey = os.environ['WEATHER_API_KEY']\nreturn ToolResult.text(key)",
        },
    )
    assert res.is_error
    assert "ENV_READ" in res.details["refused"]
    assert not (tmp_path / "note-taker" / "plugins" / "weather").exists(), (
        "a refused tool must leave NOTHING on disk — a half-written plugin is a broken agent"
    )


def test_the_refusal_names_the_fix_and_hands_the_choice_back(tmp_path):
    """Same shape as create_agent's already-exists refusal: say what to do, and do not offer the
    model a one-step workaround it can take on its own initiative."""
    res = _run(
        _tool(tmp_path),
        {
            "id": "fetcher",
            "agent": "note-taker",
            "name": "fetch_page",
            "code": "import requests\nreturn ToolResult.text(requests.get(params['u']).text)",
        },
    )
    assert res.is_error
    text = res.content[0].text
    assert "oneshot.text_complete" in text or "SHARED tool" in text
    assert "ask the user" in text.lower()


def test_a_SHARED_tool_is_not_refused(tmp_path):
    """The refusal is about the UNTRUSTED tier. A shared tool is the operator's own code, is
    never sandboxed, and may legitimately hold a key and dial out."""

    class _Cfg:
        plugins_dir = ""

    cfg = _Cfg()
    cfg.plugins_dir = str(tmp_path / "shared")
    tool = CreateToolTool(cfg, lambda: {"ok": True, "tools": ["fetch_page"]}, _Registry(tmp_path))
    res = _run(
        tool,
        {
            "id": "fetcher",
            "name": "fetch_page",
            "code": "import requests\nreturn ToolResult.text(requests.get(params['u']).text)",
        },
    )
    assert not res.is_error, res.content


# --- SandboxRules: the same defect, in code create_tool did not write --------


def _check(source: str):
    files = ["plugins/hand-written/plugin.toml", "plugins/hand-written/mod.py"]
    spec = type("Spec", (), {"id": "note-taker"})()
    return {
        f.code for f in SandboxRules().check(spec, {}, files, {"plugins/hand-written/mod.py": source})
    }


def test_a_hand_written_model_call_without_the_flag_is_reported():
    codes = _check("x = text_complete(model=None, prompt='hi')")
    assert "UNTRUSTED_MODEL_UNDECLARED" in codes


def test_declaring_the_flag_clears_it():
    codes = _check("needs_model = True\nx = text_complete(model=None, prompt='hi')")
    assert "UNTRUSTED_MODEL_UNDECLARED" not in codes


def test_a_plugin_with_no_model_call_is_never_flagged_for_it():
    codes = _check("return ToolResult.text('ok')")
    assert "UNTRUSTED_MODEL_UNDECLARED" not in codes


def test_undeclared_model_call_is_the_shared_predicate():
    """The rule and create_tool must answer from the SAME function, or an author gets told two
    different things about one file."""
    assert undeclared_model_call("x = text_complete(prompt='p')")
    assert not undeclared_model_call("needs_model = True\nx = text_complete(prompt='p')")

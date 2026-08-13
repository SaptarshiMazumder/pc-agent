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


def test_an_image_generation_call_is_recognised_as_image_gen():
    code = "r = generate_image(model=m, prompt=p, out_path=params['out'])"
    assert derive_model_need(code) == (True, "image-gen")


def test_the_port_spelling_counts_the_same_as_the_funnel_spelling():
    """`self.models.generate_image(...)` and `oneshot.generate_image(...)` are the same
    host-brokered call under the sandbox — the derivation must not care which the author wrote."""
    assert derive_model_need("r = self.models.generate_image(model=m, prompt=p, out_path=o)") == (
        True,
        "image-gen",
    )
    assert derive_model_need("t = self.models.text(model=m, prompt=p)") == (True, "text")
    assert derive_model_need("v = self.models.vision(model=m, prompt=p, image_paths=[f])") == (
        True,
        "vision",
    )


def test_image_gen_wins_when_a_tool_also_looks_or_talks():
    """model_kind picks the default the resolution chain uses, and only an image-gen model can
    supply the generating half — the most specific capability names the picker."""
    code = "a = vision_complete(prompt=p, image_paths=[f])\nb = generate_image(model=m, prompt=p, out_path=o)"
    assert derive_model_need(code) == (True, "image-gen")


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
        ('subprocess.Popen(["explorer.exe", p])', "SPAWN"),
        ('os.system("ffmpeg -i in.mp4 out.mp4")', "SPAWN"),
    ],
)
def test_the_unambiguous_defects_block(code, expected):
    assert [c for c, _w, _f in blocking_defects(code)] == [expected]


def test_the_host_brokered_fetch_route_does_not_block():
    """The call an author SHOULD write for an API. If the refusal caught this there would be no
    legal way to reach a network at all, and the declaration mechanism would be pointless."""
    code = (
        "from agent_runtime.infrastructure.net.outbound import fetch\n"
        "res = fetch('https://api.acme.com/x', headers={'Authorization': 'Bearer ${K}'})\n"
        "return ToolResult.text(res.text)"
    )
    assert blocking_defects(code) == []


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


def _check(source: str, plugin_toml: str = ""):
    files = ["plugins/hand-written/plugin.toml", "plugins/hand-written/mod.py"]
    spec = type("Spec", (), {"id": "note-taker"})()
    sources = {"plugins/hand-written/mod.py": source}
    if plugin_toml:
        sources["plugins/hand-written/plugin.toml"] = plugin_toml
    return {f.code for f in SandboxRules().check(spec, {}, files, sources)}


def test_a_hand_written_model_call_without_the_flag_is_reported():
    codes = _check("x = text_complete(model=None, prompt='hi')")
    assert "UNTRUSTED_MODEL_UNDECLARED" in codes


def test_declaring_the_flag_clears_it():
    codes = _check("needs_model = True\nx = text_complete(model=None, prompt='hi')")
    assert "UNTRUSTED_MODEL_UNDECLARED" not in codes


def test_a_plugin_with_no_model_call_is_never_flagged_for_it():
    codes = _check("return ToolResult.text('ok')")
    assert "UNTRUSTED_MODEL_UNDECLARED" not in codes


def test_prose_does_not_trigger_the_secrets_rule():
    """THE cry-wolf regression. A plugin whose DOCSTRING mentioned `${SECRET}` was flagged for
    describing the correct behaviour — the most carefully written line in the file. A report that
    is wrong about good code is one authors learn to skip."""
    assert "UNTRUSTED_WANTS_SECRETS" not in _check(
        '"""Uses the ${SECRET} placeholder route; never reads os.environ."""\nreturn 1'
    )


def test_a_real_env_read_still_triggers_it():
    """...and stripping must not have turned the rule into decoration. A string VALUE is kept."""
    assert "UNTRUSTED_WANTS_SECRETS" in _check('k = os.environ["ACME_API_KEY"]')


def test_a_declared_fetch_is_not_reported_as_wanting_network():
    """A plugin using the brokered route AND declaring its hosts is doing the right thing.
    Warning about it would be warning about the fix."""
    src = (
        "from agent_runtime.infrastructure.net.outbound import fetch\n"
        "res = fetch('https://api.acme.com/x')"
    )
    codes = _check(src, plugin_toml='[sandbox]\nnet = ["api.acme.com"]\n')
    assert "UNTRUSTED_WANTS_NETWORK" not in codes


def test_an_undeclared_url_is_still_reported():
    src = "res = fetch('https://api.acme.com/x')"
    assert "UNTRUSTED_WANTS_NETWORK" in _check(src)


def test_a_raw_client_is_reported_even_when_hosts_are_declared():
    """Declaring hosts does not buy you a socket. `import requests` cannot work, ever."""
    codes = _check("import requests", plugin_toml='[sandbox]\nnet = ["api.acme.com"]\n')
    assert "UNTRUSTED_WANTS_NETWORK" in codes


def test_a_spawn_is_reported():
    """The comfyui case: a shipped agent revealed files with subprocess.Popen(['explorer.exe'])."""
    assert "UNTRUSTED_WANTS_SPAWN" in _check('subprocess.Popen(["explorer.exe", "/select,", p])')


def test_undeclared_model_call_is_the_shared_predicate():
    """The rule and create_tool must answer from the SAME function, or an author gets told two
    different things about one file."""
    assert undeclared_model_call("x = text_complete(prompt='p')")
    assert not undeclared_model_call("needs_model = True\nx = text_complete(prompt='p')")

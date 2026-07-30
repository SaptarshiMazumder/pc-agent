"""verify_answer tool: verdict formatting, registration, and prompt guidance."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from verify_tool import VerifyTool

from agent_runtime.application.interfaces.verifier import Verdict


class _StubVerifier:
    def __init__(self, verdict):
        self.verdict = verdict
        self.last_ctx = None

    async def verify(self, ctx):
        self.last_ctx = ctx
        return self.verdict


@pytest.mark.asyncio
async def test_verify_tool_pass():
    tool = VerifyTool(SimpleNamespace(), _StubVerifier(Verdict(ok=True)))
    res = await tool.execute("c", {"answer": "the answer"}, asyncio.Event())
    assert not res.is_error
    assert "PASS" in res.content[0].text


@pytest.mark.asyncio
async def test_verify_tool_needs_work_surfaces_reasons():
    stub = _StubVerifier(Verdict(ok=False, reasons="only 3 of 5 items"))
    tool = VerifyTool(SimpleNamespace(), stub)
    res = await tool.execute(
        "c", {"answer": "3 items", "task": "5 items", "evidence": "found A,B,C"}, asyncio.Event()
    )
    txt = res.content[0].text
    assert "NEEDS WORK" in txt and "3 of 5 items" in txt
    assert "do not apologize" in txt.lower()
    # ctx was built from params
    assert stub.last_ctx.task == "5 items" and stub.last_ctx.evidence == ["found A,B,C"]


def test_verify_tool_registered_only_when_enabled():
    # verify migrated to the built-in 'verify' bundle (plugins/verify/); its config-gating now
    # lives in the plugin's register(), so assert against that directly.
    import verify_plugin

    from agent_runtime.config import load_config

    class _Api:
        def __init__(self):
            self.tools = []

        def register_tool(self, t):
            self.tools.append(t)

        def register_prompt_section(self, section):
            pass

    class _Ctx:
        def __init__(self, cfg):
            self.config = cfg

    cfg = load_config()
    cfg.verify_tool = False
    api = _Api()
    verify_plugin.register(api, _Ctx(cfg))
    assert not any(t.name == "verify_answer" for t in api.tools)

    cfg.verify_tool = True
    cfg.verify_model = "gemini/gemini-2.5-flash"
    api = _Api()
    verify_plugin.register(api, _Ctx(cfg))
    assert any(t.name == "verify_answer" for t in api.tools)


def test_verify_prompt_section_only_when_tool_present():
    # the ## Verify directive is a prompt section contributed by the verify bundle, gated on the tool.
    import verify_plugin

    assert "## Verify Before You Send" in verify_plugin._verify_section(
        [SimpleNamespace(name="verify_answer")], None, None
    )
    assert verify_plugin._verify_section([SimpleNamespace(name="read")], None, None) == ""

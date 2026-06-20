"""verify_answer tool: verdict formatting, registration, and prompt guidance."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.application.interfaces.verifier import Verdict
from agentd.infrastructure.tools.verify_tool import VerifyTool


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
    res = await tool.execute("c", {"answer": "3 items", "task": "5 items",
                                    "evidence": "found A,B,C"}, asyncio.Event())
    txt = res.content[0].text
    assert "NEEDS WORK" in txt and "3 of 5 items" in txt
    assert "do not apologize" in txt.lower()
    # ctx was built from params
    assert stub.last_ctx.task == "5 items" and stub.last_ctx.evidence == ["found A,B,C"]


def test_verify_tool_registered_only_in_tool_mode():
    from agentd.config import load_config
    from agentd.infrastructure.tools import build_tools

    cfg = load_config()
    cfg.verify_tool = False
    assert not any(t.name == "verify_answer" for t in build_tools(cfg))

    cfg.verify_tool = True
    cfg.verify_model = "gemini/gemini-2.5-flash"
    assert any(t.name == "verify_answer" for t in build_tools(cfg))


def test_verify_prompt_section_only_when_tool_present():
    from agentd.config import load_config
    from agentd.infrastructure.prompt import build_system_prompt

    cfg = load_config()
    tool = VerifyTool(cfg, _StubVerifier(Verdict(ok=True)))
    with_tool = build_system_prompt(cfg, [tool], cfg.model, "medium", skills=[])
    without = build_system_prompt(cfg, [], cfg.model, "medium", skills=[])
    assert "## Verify Before You Send" in with_tool
    assert "Verify Before You Send" not in without

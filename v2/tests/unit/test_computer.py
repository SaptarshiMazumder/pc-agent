import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.infrastructure.tools.computer.actions import (
    parse_function_call,
    to_pyautogui_key,
    to_real,
)
from agentd.infrastructure.tools.computer.drivers.gemini_computer_use import (
    GeminiComputerUseDriver,
)
from agentd.infrastructure.tools.computer.factory import build_computer_provider

# ---- pure helpers -----------------------------------------------------


def test_to_real_corners_and_center():
    region = (0, 0, 1920, 1080)
    assert to_real(0, 0, region) == (0, 0)
    assert to_real(1000, 1000, region) == (1920, 1080)
    assert to_real(500, 500, region) == (960, 540)


def test_to_real_with_offset_origin():
    region = (-1920, 0, 1920, 1080)  # second monitor to the left (virtual desktop)
    assert to_real(0, 0, region) == (-1920, 0)
    assert to_real(1000, 0, region) == (0, 0)


def test_key_mapping():
    assert to_pyautogui_key("Control") == "ctrl"
    assert to_pyautogui_key("control") == "ctrl"
    assert to_pyautogui_key("Return") == "enter"
    assert to_pyautogui_key("escape") == "esc"
    assert to_pyautogui_key("A") == "a"  # unknown -> lowercased


def test_parse_function_call():
    fc = SimpleNamespace(name="click_at", args={"x": 500, "y": 600})
    assert parse_function_call(fc) == ("click_at", {"x": 500, "y": 600})
    assert parse_function_call(SimpleNamespace(name="x", args=None)) == ("x", {})


# ---- factory ----------------------------------------------------------


def test_factory_disabled_returns_none():
    assert build_computer_provider(SimpleNamespace(computer_enabled=False)) is None


# ---- the driver loop (fake model + fake provider; no real desktop) ----


class FakeProvider:
    region = (0, 0, 1000, 1000)

    def __init__(self, raise_on=None):
        self.acts = []
        self._raise_on = raise_on

    def screenshot(self) -> bytes:
        return b"\x89PNG-fake"

    def act(self, action, **params):
        self.acts.append((action, params))
        if self._raise_on and action == self._raise_on:
            raise RuntimeError("failsafe")
        return {}


def _fc(name, **args):
    # a Content part carrying a function_call (mirrors google-genai Part shape)
    return SimpleNamespace(text=None, function_call=SimpleNamespace(name=name, args=args))


def _resp(parts):
    content = SimpleNamespace(parts=parts, role="model")
    return SimpleNamespace(candidates=[SimpleNamespace(content=content)])


def _scripted_gen(turns):
    seq = list(turns)

    def gen(contents):
        return _resp(seq.pop(0))

    return gen


def _always(parts):
    def gen(contents):
        return _resp(list(parts))

    return gen


def _cfg(tmp_path, max_steps=25, save_screenshots=False):
    return SimpleNamespace(
        state_dir=tmp_path,
        plugins={
            "computer": {
                "tools": {
                    "computer": {
                        "max_steps": max_steps,
                        "save_screenshots": save_screenshots,
                    }
                }
            }
        },
    )


async def _run(provider, gen, tmp_path, task="do it", max_steps=25, abort=None):
    d = GeminiComputerUseDriver(provider, "m", _cfg(tmp_path, max_steps), generate_fn=gen)
    return await d.run(task, abort or asyncio.Event())


@pytest.mark.asyncio
async def test_executes_actions_then_returns_summary(tmp_path):
    p = FakeProvider()
    gen = _scripted_gen(
        [
            [_fc("click_at", x=500, y=500)],
            [_fc("type_text_at", x=500, y=500, text="hello")],
            [SimpleNamespace(text="Done: typed hello.", function_call=None)],
        ]
    )
    summary = await _run(p, gen, tmp_path)
    assert summary == "Done: typed hello."
    assert [a[0] for a in p.acts] == ["click_at", "type_text_at"]
    # default: screenshots are sent to the model only — never written to disk
    assert not any(tmp_path.rglob("*.png"))


@pytest.mark.asyncio
async def test_saves_screenshots_when_flag_on(tmp_path):
    p = FakeProvider()
    gen = _scripted_gen(
        [
            [_fc("click_at", x=1, y=1)],
            [SimpleNamespace(text="done", function_call=None)],
        ]
    )
    d = GeminiComputerUseDriver(p, "m", _cfg(tmp_path, save_screenshots=True), generate_fn=gen)
    await d.run("task", asyncio.Event())
    assert list(tmp_path.rglob("step-*.png"))  # step 0 + action step persisted


@pytest.mark.asyncio
async def test_text_only_first_turn_terminates(tmp_path):
    p = FakeProvider()
    gen = _scripted_gen([[SimpleNamespace(text="Nothing to do.", function_call=None)]])
    summary = await _run(p, gen, tmp_path)
    assert summary == "Nothing to do."
    assert p.acts == []


@pytest.mark.asyncio
async def test_abort_before_first_step(tmp_path):
    p = FakeProvider()
    ab = asyncio.Event()
    ab.set()
    gen = _always([_fc("click_at", x=1, y=1)])
    summary = await _run(p, gen, tmp_path, abort=ab)
    assert "Aborted" in summary
    assert p.acts == []


@pytest.mark.asyncio
async def test_step_cap(tmp_path):
    p = FakeProvider()
    gen = _always([_fc("click_at", x=1, y=1)])  # never stops calling
    summary = await _run(p, gen, tmp_path, max_steps=3)
    assert "step cap" in summary.lower()
    assert len(p.acts) == 3


@pytest.mark.asyncio
async def test_model_call_error_ends_run(tmp_path):
    # a hung/timed-out model call (the http_options/backstop path) surfaces as a
    # clean error string, not a hang.
    p = FakeProvider()

    def boom(contents):
        raise TimeoutError("deadline exceeded (504)")

    d = GeminiComputerUseDriver(p, "m", _cfg(tmp_path), generate_fn=boom)
    summary = await d.run("task", asyncio.Event())
    assert "Computer-use model error" in summary and "TimeoutError" in summary
    assert p.acts == []  # never reached an action


@pytest.mark.asyncio
async def test_failsafe_stops_run(tmp_path):
    p = FakeProvider(raise_on="click_at")
    gen = _always([_fc("click_at", x=1, y=1)])
    summary = await _run(p, gen, tmp_path)
    assert "Stopped" in summary and "RuntimeError" in summary
    assert len(p.acts) == 1  # stopped after the first failing action

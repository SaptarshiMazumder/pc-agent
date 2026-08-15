"""A tool result is CONTEXT, and context is finite.

Not a style concern. A tool's output is re-sent to the model on every later turn for the rest of
the session, so an oversized result is not paid once — it is paid on every turn that follows.

Observed, in one real build: two `grep` calls returned 92KB between them and a single `verify_app`
screenshot added 114KB as an inline image. The conversation reached the model's context limit and
every further message came back empty, which reads as "the model is broken" and is really "there
is nothing left to think with".

Both tools reported themselves as capped while doing it. The caps counted the wrong thing.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


# --------------------------------------------------------------------------- grep


def _grep(tmp_path: Path, **params):
    from plugins.core_fs.grep_tool import GrepTool

    class Config:
        workspace = str(tmp_path)  # the root `path` is resolved against

    tool = GrepTool(Config())
    return asyncio.run(
        tool.execute("id", {"pattern": "MATCH", "path": str(tmp_path), **params}, asyncio.Event())
    )


def _text(result) -> str:
    return "".join(getattr(b, "text", "") for b in result.content)


def test_a_broad_search_over_long_lines_stays_within_budget(tmp_path):
    """THE CASE THAT BROKE A RUN: a line cap of 200 with a 400-character line cap is 80KB, and
    the search reported itself as capped the whole time."""
    from plugins.core_fs.grep_tool import MAX_OUTPUT_BYTES

    big = tmp_path / "big.txt"
    big.write_text("\n".join(f"MATCH {'x' * 600}" for _ in range(500)), encoding="utf-8")

    body = _text(_grep(tmp_path, max_results=500))

    assert len(body) < MAX_OUTPUT_BYTES * 1.5, f"grep returned {len(body)} bytes"


def test_it_says_which_limit_stopped_it(tmp_path):
    """"Raise max_results" is useless advice when the BYTES ran out — a bigger count returns the
    same bytes and the caller burns another call learning that."""
    big = tmp_path / "big.txt"
    big.write_text("\n".join(f"MATCH {'x' * 600}" for _ in range(500)), encoding="utf-8")

    body = _text(_grep(tmp_path, max_results=500))

    assert "KB of output" in body
    assert "will not help" in body


def test_long_lines_are_truncated_not_dropped(tmp_path):
    """The match matters even when the line is minified junk — the file and line number are the
    answer, and the rest of a 600-character line is not."""
    (tmp_path / "a.js").write_text(f"var x = 1; MATCH {'y' * 900}", encoding="utf-8")

    body = _text(_grep(tmp_path))

    assert "a.js" in body
    assert "…" in body


def test_a_small_search_is_untouched(tmp_path):
    """The budget must not distort ordinary use, which is nearly all of it."""
    (tmp_path / "a.py").write_text("first MATCH here\nsecond MATCH there\n", encoding="utf-8")

    body = _text(_grep(tmp_path))

    assert "first MATCH here" in body
    assert "second MATCH there" in body
    assert "CAPPED" not in body


# --------------------------------------------------------------------------- verify_app


def test_a_passing_verification_does_not_attach_its_screenshot(tmp_path, monkeypatch):
    """An attached image lives in the conversation for the rest of the session. A window that
    passed every check does not need its portrait kept forever — the PATH is enough, and the
    model can ask for the image when the question is actually visual."""
    from test_verify_app import FakeDriver, _agent, _healthy, _service  # same-directory helper

    from agent_authoring.presentation.verify_app_tool import VerifyAppTool

    _agent(tmp_path)
    shot = _healthy(screenshot=str(tmp_path / "shot.jpg"))
    tool = VerifyAppTool(_service(tmp_path, FakeDriver(shot)))

    result = asyncio.run(tool.execute("c", {"agent_id": "known"}, asyncio.Event()))

    assert result.artifacts == []
    assert "shot.jpg" in _text(result), "the path must still be reported"
    assert "screenshot='always'" in _text(result), "and how to actually see it"


def test_a_failing_verification_does_attach_it(tmp_path, monkeypatch):
    """This is the case the image exists for: something is wrong and the layout may be why."""
    from test_verify_app import FakeDriver, _agent, _healthy, _service

    from agent_authoring.presentation.verify_app_tool import VerifyAppTool

    _agent(tmp_path)
    broken = _healthy(
        text="", screenshot=str(tmp_path / "shot.jpg")
    )  # blank render => an error finding
    tool = VerifyAppTool(_service(tmp_path, FakeDriver(broken)))

    result = asyncio.run(tool.execute("c", {"agent_id": "known"}, asyncio.Event()))

    assert result.artifacts == [str(tmp_path / "shot.jpg")]


@pytest.mark.parametrize("mode,expected", [("always", 1), ("never", 0)])
def test_the_caller_can_override(tmp_path, mode, expected):
    from test_verify_app import FakeDriver, _agent, _healthy, _service

    from agent_authoring.presentation.verify_app_tool import VerifyAppTool

    _agent(tmp_path)
    shot = _healthy(screenshot=str(tmp_path / "shot.jpg"))
    tool = VerifyAppTool(_service(tmp_path, FakeDriver(shot)))

    result = asyncio.run(
        tool.execute("c", {"agent_id": "known", "screenshot": mode}, asyncio.Event())
    )

    assert len(result.artifacts) == expected

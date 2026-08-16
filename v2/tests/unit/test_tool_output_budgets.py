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


def test_the_tree_is_the_default_evidence_not_a_picture(tmp_path):
    """~2KB against ~114KB, and it answers more: roles, labels, disabled states. The image only
    wins on questions about pixels, and it is re-sent on every later turn."""
    from test_verify_app import FakeDriver, _agent, _healthy, _service  # same-directory helper

    from agent_authoring.presentation.verify_app_tool import VerifyAppTool

    _agent(tmp_path)
    seen = _healthy(snapshot='- navigation:\n  - button "Chat"\n  - button "Settings" [disabled]')
    driver = FakeDriver(seen)
    tool = VerifyAppTool(_service(tmp_path, driver))

    result = asyncio.run(tool.execute("c", {"agent_id": "known"}, asyncio.Event()))

    assert result.artifacts == [], "no image unless asked for"
    assert driver.want_shot is False, "and none TAKEN — the cost is in capturing it"
    body = _text(result)
    assert 'button "Chat"' in body and "[disabled]" in body


def test_asking_for_a_picture_takes_and_attaches_one(tmp_path):
    """For the one thing the tree cannot show: how it looks."""
    from test_verify_app import FakeDriver, _agent, _healthy, _service

    from agent_authoring.presentation.verify_app_tool import VerifyAppTool

    _agent(tmp_path)
    driver = FakeDriver(_healthy(screenshot=str(tmp_path / "shot.jpg")))
    tool = VerifyAppTool(_service(tmp_path, driver))

    result = asyncio.run(
        tool.execute("c", {"agent_id": "known", "screenshot": True}, asyncio.Event())
    )

    assert driver.want_shot is True
    assert result.artifacts == [str(tmp_path / "shot.jpg")]


def test_a_huge_tree_is_capped_and_says_so(tmp_path):
    """A silently truncated structure reads as a page that ends where the text stops."""
    from test_verify_app import FakeDriver, _agent, _healthy, _service

    from agent_authoring.presentation.verify_app_tool import MAX_SNAPSHOT_CHARS, VerifyAppTool

    _agent(tmp_path)
    huge = _healthy(snapshot="- button \"x\"\n" * 5000)
    tool = VerifyAppTool(_service(tmp_path, FakeDriver(huge)))

    body = _text(asyncio.run(tool.execute("c", {"agent_id": "known"}, asyncio.Event())))

    assert len(body) < MAX_SNAPSHOT_CHARS * 1.3
    assert "more characters of tree, not shown" in body

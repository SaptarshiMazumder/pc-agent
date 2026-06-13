import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.tools.fs_tools import apply_edits


def test_single_edit():
    new, diff = apply_edits("hello world\n", [{"oldText": "world", "newText": "there"}])
    assert new == "hello there\n"
    assert "-hello world" in diff and "+hello there" in diff


def test_multiple_edits():
    src = "a = 1\nb = 2\nc = 3\n"
    new, _ = apply_edits(
        src,
        [
            {"oldText": "a = 1", "newText": "a = 10"},
            {"oldText": "c = 3", "newText": "c = 30"},
        ],
    )
    assert new == "a = 10\nb = 2\nc = 30\n"


def test_no_match_raises_with_hint():
    with pytest.raises(ValueError, match="not found"):
        apply_edits("alpha\nbeta\n", [{"oldText": "gamma", "newText": "x"}])


def test_ambiguous_match_raises():
    with pytest.raises(ValueError, match="must be unique"):
        apply_edits("x\nx\n", [{"oldText": "x", "newText": "y"}])


def test_overlapping_edits_raise():
    with pytest.raises(ValueError, match="overlap"):
        apply_edits(
            "abcdef",
            [
                {"oldText": "abcd", "newText": "1"},
                {"oldText": "cdef", "newText": "2"},
            ],
        )


def test_crlf_normalization_and_restoration():
    src = "line1\r\nline2\r\n"
    new, _ = apply_edits(src, [{"oldText": "line2", "newText": "line2 edited"}])
    assert new == "line1\r\nline2 edited\r\n"


def test_crlf_in_old_text():
    src = "line1\nline2\n"
    new, _ = apply_edits(src, [{"oldText": "line1\r\nline2", "newText": "merged"}])
    assert new == "merged\n"


def test_empty_old_text_raises():
    with pytest.raises(ValueError, match="must not be empty"):
        apply_edits("abc", [{"oldText": "", "newText": "x"}])

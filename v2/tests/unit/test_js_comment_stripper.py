"""Comments are not code — but a `/` is not always a comment.

`UiRules` reads generated `ui/*.js` looking for protocol mistakes. It fired on this line:

    // Reading `payload.type` makes every branch miss and the screen never updates.

i.e. it flagged a file for the bug that file was warning about. A check that is wrong on the
most carefully written code it will ever see is a check people turn off.

The stripper that fixes it has one real risk: over-stripping. If it mistakes CODE for a
comment, a genuine defect disappears silently and the rule becomes decoration. So the tests
below run in both directions — comments must go, and everything that merely looks like one
must stay.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from agent_authoring.domain.js_comment_stripper import JsCommentStripper

STRIP = JsCommentStripper().strip


def test_offsets_survive():
    """Comments are blanked, not deleted, so a position in the result still points at the same
    place in the file on disk."""
    src = "const a = 1 // note\nconst b = 2\n"
    out = STRIP(src)
    assert len(out) == len(src)
    assert out.count("\n") == src.count("\n")


# ── comments go ─────────────────────────────────────────────────────────────
def test_a_line_comment_goes():
    assert "payload.type" not in STRIP("const x = 1 // reading payload.type is wrong\n")


def test_a_block_comment_goes():
    src = "/* the type is payload.event.type, not payload.type */\nconst x = 1"
    assert "payload.type" not in STRIP(src)
    assert "const x = 1" in STRIP(src)


def test_a_doc_comment_spanning_lines_goes():
    src = """
    /** Never do this:
     *    if (payload.type === 'message_delta')
     */
    const ev = payload.event
    """
    out = STRIP(src)
    assert "message_delta" not in out
    assert "payload.event" in out


# ── code that merely looks like a comment stays ─────────────────────────────
def test_a_url_in_a_string_is_not_a_comment():
    src = "const u = 'https://example.com/x'\nconst t = payload.type\n"
    out = STRIP(src)
    assert "https://example.com/x" in out
    assert "payload.type" in out, "the // in the URL swallowed the next line"


@pytest.mark.parametrize("quote", ["'", '"', "`"])
def test_a_comment_marker_inside_any_string_is_literal(quote):
    src = f"const s = {quote}// not a comment{quote}\nconst t = payload.type\n"
    out = STRIP(src)
    assert "not a comment" in out
    assert "payload.type" in out


def test_a_regex_containing_a_backtick_does_not_open_a_template_literal():
    """md.js really does this — `src.split(/`([^`]+)`/)`. Treating that backtick as the start
    of a template literal swallows the rest of the file."""
    src = "src.split(/`([^`]+)`/)\nconst t = payload.type\n"
    assert "payload.type" in STRIP(src)


def test_a_regex_containing_a_slash_does_not_end_early():
    src = "const re = /[^/]+/g\nconst t = payload.type\n"
    assert "payload.type" in STRIP(src)


def test_a_regex_containing_a_quote_does_not_open_a_string():
    src = """const re = /['"]/g\nconst t = payload.type\n"""
    assert "payload.type" in STRIP(src)


def test_division_is_not_a_regex():
    src = "const half = total / 2\nconst t = payload.type\n"
    out = STRIP(src)
    assert "total / 2" in out
    assert "payload.type" in out


def test_a_comment_inside_a_template_hole_still_goes():
    src = "const s = `a ${b /* payload.type */} c`\nconst t = payload.type\n"
    out = STRIP(src)
    assert out.count("payload.type") == 1, "the one in the ${} hole should have gone"


def test_a_template_literal_with_a_nested_expression_closes_properly():
    src = "const s = `x ${a ? 'y' : 'z'} w`\nconst t = payload.type\n"
    assert "payload.type" in STRIP(src)


def test_an_escaped_quote_does_not_end_the_string():
    src = "const s = 'it\\'s fine // really'\nconst t = payload.type\n"
    assert "payload.type" in STRIP(src)


# ── the rule it exists for still catches the real thing ─────────────────────
def test_the_real_defect_is_still_visible_after_stripping():
    """The whole point is that stripping removes prose, not branches."""
    src = """
    // the type is nested — read payload.event.type
    client.onRun(key, (payload) => {
      if (payload.type === 'tool_execution_start') start()
    })
    """
    out = STRIP(src)
    assert "if (payload.type === 'tool_execution_start')" in out

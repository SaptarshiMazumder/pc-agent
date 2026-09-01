"""The .env value parser strips inline comments + surrounding quotes, so a commented
flag like `AGENTD_NOTIFY=1   # on` yields "1" (and exact-match bools stay correct),
while values that legitimately contain '#' (hex colors, '#' inside quotes) are kept."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.config import _dotenv_value


def test_strips_inline_comment():
    assert _dotenv_value("1                  # outbound notifications") == "1"
    assert _dotenv_value("30m   # interval") == "30m"


def test_no_comment_unchanged():
    assert _dotenv_value("gemini/gemini-2.5-flash") == "gemini/gemini-2.5-flash"
    assert _dotenv_value("callrate,noprogress") == "callrate,noprogress"
    assert _dotenv_value("0") == "0"


def test_leading_hash_is_kept_not_a_comment():
    # only a '#' AFTER whitespace is a comment; a value that starts with '#' is literal
    assert _dotenv_value("#fff") == "#fff"
    assert _dotenv_value("#abc   # real comment") == "#abc"


def test_quoted_value_preserves_inner_hash():
    assert _dotenv_value('"a # b"') == "a # b"
    assert _dotenv_value("'pass#word'") == "pass#word"
    assert _dotenv_value('"1"   # trailing comment ignored') == "1"


def test_strips_surrounding_quotes():
    assert _dotenv_value('"value"') == "value"
    assert _dotenv_value("'value'") == "value"


def test_tab_before_hash_is_a_comment():
    assert _dotenv_value("1\t# tabbed comment") == "1"


def test_exact_match_bool_survives_comment():
    # the real bug this fixes: AGENTD_NOTIFY/AUTONOMY use exact `in ("1","true",...)`,
    # so the comment MUST be stripped or the flag silently flips off.
    assert _dotenv_value("1   # on").lower() in ("1", "true", "yes", "on")

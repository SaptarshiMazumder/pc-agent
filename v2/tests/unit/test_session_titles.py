"""Auto-titling: output cleanup + snippet fallback + no-throw guarantee."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.infrastructure import session_titles


def test_clean_title_strips_noise():
    assert session_titles.clean_title('Title: "Apple One-Word Reply."') == "Apple One-Word Reply"
    assert session_titles.clean_title("  centering a div  \n") == "centering a div"
    assert session_titles.clean_title('"Quoted Thing"') == "Quoted Thing"
    assert len(session_titles.clean_title("word " * 40)) <= session_titles.MAX_TITLE_CHARS


def test_snippet_title_truncates():
    assert session_titles.snippet_title("hi there") == "hi there"
    long = "make me a publication grade figure of a mitochondrion with labels"
    out = session_titles.snippet_title(long, limit=30)
    assert len(out) <= 31 and out.endswith("…")


def test_generate_title_falls_back_to_snippet_on_error(monkeypatch):
    def boom(**_kwargs):
        raise RuntimeError("model unreachable")

    monkeypatch.setattr(session_titles, "text_complete", boom)
    title = session_titles.generate_title("Center a div in CSS please", "Use flexbox", "any/model")
    assert title.startswith("Center a div")  # snippet fallback, never raises


def test_generate_title_uses_model_output(monkeypatch):
    monkeypatch.setattr(session_titles, "text_complete", lambda **_k: "  Centering A Div.  ")
    assert (
        session_titles.generate_title("how do I center a div", "flexbox", "any/model")
        == "Centering A Div"
    )


def test_generate_title_empty_user_returns_empty():
    assert session_titles.generate_title("", "", "any/model") == ""

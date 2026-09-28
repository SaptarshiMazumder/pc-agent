"""A model on the approval card is an option FOR A STEP; options of one step are alternatives."""

from __future__ import annotations

from ask_user_tool import normalise, render


def _svc(name, step="", credits=100):
    row = {"name": name, "purpose": "does it", "usd": 1.0, "credits": credits}
    if step:
        row["step"] = step
    return row


def test_each_option_keeps_its_step():
    ask, error = normalise({"services": [_svc("FLUX VTO", "tryon"), _svc("Qwen local", "tryon", 0),
                                         _svc("Nano Banana 2", "campaign")]})
    assert not error
    assert [s.get("step") for s in ask["services"]] == ["tryon", "tryon", "campaign"]
    assert "[step tryon] FLUX VTO" in render(ask)


def test_the_same_model_may_serve_two_steps_but_not_one_twice():
    ok, error = normalise({"services": [_svc("Nano Banana Pro", "tryon"), _svc("Nano Banana Pro", "campaign")]})
    assert ok and not error
    _, error = normalise({"services": [_svc("Nano Banana Pro", "tryon"), _svc("Nano Banana Pro", "tryon")]})
    assert "listed twice" in error


def test_a_row_without_a_step_stays_as_it_was():
    ask, _ = normalise({"services": [_svc("Topaz")]})
    assert "step" not in ask["services"][0]

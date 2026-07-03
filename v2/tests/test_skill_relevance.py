"""Relevance-filtered skills (optional): advertise only the top-K skills most related to the
message. Pure ranker tested with a deterministic fake embedder; FAIL-OPEN on any embed error."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.application.interfaces.skills import Skill
from agentd.infrastructure.skills.relevance import rank_skills_by_relevance


def _sk(name, always=False):
    return Skill(name=name, description=f"{name} things", path=f"/p/{name}/SKILL.md", always=always)


_VOCAB = ("alpha", "beta", "gamma", "delta", "epsilon")


def _vocab_embed(texts):
    """A deterministic embedder: one-hot over a fixed vocab. A text's vector marks which vocab
    words it contains, so a skill whose name is in the query aligns with the query vector."""
    return [[1.0 if w in t else 0.0 for w in _VOCAB] for t in texts]


def test_keeps_only_top_k_most_relevant():
    skills = [_sk("alpha"), _sk("beta"), _sk("gamma")]
    out = rank_skills_by_relevance(skills, "help with beta", _vocab_embed, top_k=1)
    assert [s.name for s in out] == ["beta"]


def test_always_on_skills_are_never_dropped():
    skills = [_sk("alpha"), _sk("beta"), _sk("epsilon", always=True)]
    out = rank_skills_by_relevance(skills, "about beta", _vocab_embed, top_k=2)
    names = [s.name for s in out]
    assert "epsilon" in names and "beta" in names and "alpha" not in names   # always-on kept + top match


def test_preserves_original_order():
    skills = [_sk("alpha"), _sk("beta"), _sk("gamma")]
    out = rank_skills_by_relevance(skills, "alpha gamma please", _vocab_embed, top_k=2)
    assert [s.name for s in out] == ["alpha", "gamma"]                    # input order, not score order


def test_noop_when_already_small_or_disabled():
    skills = [_sk("a"), _sk("b")]
    assert rank_skills_by_relevance(skills, "q", _vocab_embed, top_k=5) is skills   # <= top_k
    assert rank_skills_by_relevance(skills, "", _vocab_embed, top_k=1) is skills    # no query
    assert rank_skills_by_relevance(skills, "q", None, top_k=1) is skills           # no embedder


def test_fails_open_on_embed_error():
    skills = [_sk("a"), _sk("b"), _sk("c")]

    def boom(texts):
        raise RuntimeError("embedding endpoint down")

    out = rank_skills_by_relevance(skills, "q", boom, top_k=1)
    assert out is skills                                                  # all advertised, no skill lost


def test_build_embed_fn_none_unless_enabled_and_model():
    from types import SimpleNamespace

    from agentd.infrastructure.skills.relevance import build_skill_embed_fn

    def _cfg(enabled, model):
        plugins = {"skills": {"tools": {"relevance": {"model": model}}}} if model else {}
        return SimpleNamespace(skills_relevance_enabled=enabled, plugins=plugins)

    assert build_skill_embed_fn(_cfg(False, "text-embed")) is None      # disabled
    assert build_skill_embed_fn(_cfg(True, "")) is None                 # no model => off
    assert build_skill_embed_fn(_cfg(True, "text-embed")) is not None   # enabled + model

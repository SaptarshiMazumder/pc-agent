"""Agent display presentation: generation cleanup, sidecar IO, registry precedence."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.infrastructure.agents import presentation


def test_clean_presentation_trims_and_caps():
    out = presentation.clean_presentation({
        "tagline": '  "Finance ·  Gmail."  ',
        "suggestions": ["  Check this month's spend.  ", "", "b" * 200, "three", "four"],
    })
    assert out["tagline"] == "Finance · Gmail"
    assert len(out["suggestions"]) == 3                    # capped at 3, empty dropped
    assert out["suggestions"][0] == "Check this month's spend"
    assert len(out["suggestions"][1]) <= presentation.MAX_SUGGESTION_CHARS
    assert presentation.clean_presentation({"tagline": ""}) == {}
    assert presentation.clean_presentation("nope") == {}


def test_generate_parses_json_with_noise(monkeypatch):
    monkeypatch.setattr(presentation, "text_complete", lambda **_k: (
        'Sure! Here you go:\n{"tagline": "front desk", '
        '"suggestions": ["Book a table for two", "Check today\'s reservations"]}\nDone.'))
    out = presentation.generate_presentation("Sakana", "sushi front desk", "IDENTITY...", "m")
    assert out["tagline"] == "front desk"
    assert out["suggestions"] == ["Book a table for two", "Check today's reservations"]


def test_generate_never_raises(monkeypatch):
    def boom(**_k):
        raise RuntimeError("model down")
    monkeypatch.setattr(presentation, "text_complete", boom)
    assert presentation.generate_presentation("X", "d", "identity", "m") == {}
    monkeypatch.setattr(presentation, "text_complete", lambda **_k: "no json here")
    assert presentation.generate_presentation("X", "d", "identity", "m") == {}
    # nothing to describe -> no call at all
    assert presentation.generate_presentation("X", "", "", "m") == {}


def test_sidecar_roundtrip(tmp_path):
    assert presentation.read_sidecar(tmp_path) == {}
    assert presentation.read_sidecar(None) == {}
    presentation.write_sidecar(tmp_path, {"tagline": "decks · video", "suggestions": ["a"]})
    assert presentation.read_sidecar(tmp_path)["tagline"] == "decks · video"


def _registry(tmp_path):
    from agentd.infrastructure.agents.file_registry import FileAgentRegistry
    return FileAgentRegistry(SimpleNamespace(
        state_dir=tmp_path / "state", agents_dir=tmp_path / "agents", agent_name="jarvis"))


def test_registry_reads_sidecar_and_toml_wins(tmp_path):
    d = tmp_path / "agents" / "helper"
    d.mkdir(parents=True)
    (d / "agent.toml").write_text('name = "Helper"\n', encoding="utf-8")
    (d / "presentation.json").write_text(json.dumps(
        {"tagline": "generated line", "suggestions": ["Do the thing"]}), encoding="utf-8")

    spec = _registry(tmp_path).get("helper")
    assert spec.tagline == "generated line"               # sidecar fills the gap
    assert spec.suggestions == ("Do the thing",)

    # authored agent.toml fields WIN over the generated sidecar
    (d / "agent.toml").write_text(
        'name = "Helper"\ntagline = "authored line"\nsuggestions = ["One", "Two"]\n',
        encoding="utf-8")
    spec = _registry(tmp_path).get("helper")
    assert spec.tagline == "authored line"
    assert spec.suggestions == ("One", "Two")


def test_agents_list_carries_presentation(tmp_path):
    from agentd.presentation.gateway import Gateway

    spec = SimpleNamespace(name="Helper", version="1", tagline="finance · gmail",
                           suggestions=("Check spend",))
    gw = Gateway(config=SimpleNamespace(state_dir=tmp_path, agent_id="main",
                                        agent_name="jarvis"),
                 service=None,
                 registry=SimpleNamespace(list_ids=lambda: ["helper"],
                                          get=lambda a: spec))
    agents = gw._agents_list()["agents"]
    assert agents[0]["tagline"] == "finance · gmail"
    assert agents[0]["suggestions"] == ["Check spend"]

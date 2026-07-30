"""Agent display presentation: generation cleanup, sidecar IO, registry precedence."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.infrastructure.agents import presentation


def test_clean_presentation_trims_and_caps():
    out = presentation.clean_presentation(
        {
            "tagline": '  "Finance ·  Gmail."  ',
            "suggestions": ["  Check this month's spend.  ", "", "b" * 200, "three", "four"],
        }
    )
    assert out["tagline"] == "Finance · Gmail"
    assert len(out["suggestions"]) == 3  # capped at 3, empty dropped
    assert out["suggestions"][0] == "Check this month's spend"
    assert len(out["suggestions"][1]) <= presentation.MAX_SUGGESTION_CHARS
    assert presentation.clean_presentation({"tagline": ""}) == {}
    assert presentation.clean_presentation("nope") == {}


def test_generate_parses_json_with_noise(monkeypatch):
    monkeypatch.setattr(
        presentation,
        "text_complete",
        lambda **_k: (
            'Sure! Here you go:\n{"tagline": "front desk", '
            '"suggestions": ["Book a table for two", "Check today\'s reservations"]}\nDone.'
        ),
    )
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
    from agent_runtime.infrastructure.agents.file_registry import FileAgentRegistry

    return FileAgentRegistry(
        SimpleNamespace(
            state_dir=tmp_path / "state", agents_dir=tmp_path / "agents", agent_name="jarvis"
        )
    )


def test_registry_reads_sidecar_and_toml_wins(tmp_path):
    d = tmp_path / "agents" / "helper"
    d.mkdir(parents=True)
    (d / "agent.toml").write_text('name = "Helper"\n', encoding="utf-8")
    (d / "presentation.json").write_text(
        json.dumps({"tagline": "generated line", "suggestions": ["Do the thing"]}), encoding="utf-8"
    )

    spec = _registry(tmp_path).get("helper")
    assert spec.tagline == "generated line"  # sidecar fills the gap
    assert spec.suggestions == ("Do the thing",)

    # authored agent.toml fields WIN over the generated sidecar
    (d / "agent.toml").write_text(
        'name = "Helper"\ntagline = "authored line"\nsuggestions = ["One", "Two"]\n',
        encoding="utf-8",
    )
    spec = _registry(tmp_path).get("helper")
    assert spec.tagline == "authored line"
    assert spec.suggestions == ("One", "Two")


def test_agents_list_carries_presentation(tmp_path):
    from agent_runtime.presentation.gateway import Gateway

    spec = SimpleNamespace(
        name="Helper",
        version="1",
        tagline="finance · gmail",
        suggestions=("Check spend",),
        color="#84cc16",
    )
    gw = Gateway(
        config=SimpleNamespace(state_dir=tmp_path, agent_id="main", agent_name="jarvis"),
        service=None,
        registry=SimpleNamespace(list_ids=lambda: ["helper"], get=lambda a: spec),
    )
    agents = gw._agents_list()["agents"]
    assert agents[0]["tagline"] == "finance · gmail"
    assert agents[0]["suggestions"] == ["Check spend"]
    assert agents[0]["color"] == "#84cc16"


def test_hsl_hex_roundtrips_and_is_valid():
    for hue in (0, 45, 137.5, 200, 359):
        hex_color = presentation.hsl_to_hex(hue)
        assert len(hex_color) == 7 and hex_color[0] == "#"
        back = presentation.hex_to_hue(hex_color)
        assert back is not None and presentation._hue_distance(back, hue) < 2  # ~roundtrip
    assert presentation.hex_to_hue("not-a-color") is None


def test_assign_hue_keeps_agents_apart():
    # base hue comes from the id hash; a clash is pushed at least _MIN_HUE_SEP away
    taken: list[float] = []
    hues = []
    for aid in [
        "main",
        "expense-tracker",
        "figure-creator",
        "sakana-sushi",
        "cost-calc",
        "x1",
        "x2",
    ]:
        h = presentation.assign_hue(aid, taken)
        taken.append(h)
        hues.append(h)
    for i in range(len(hues)):
        for j in range(i + 1, len(hues)):
            assert presentation._hue_distance(hues[i], hues[j]) >= presentation._MIN_HUE_SEP

    # deterministic + matches the client fallback for a non-colliding id
    assert presentation.assign_hue("solo", []) == presentation._hue_from_id("solo")


def test_update_sidecar_merges(tmp_path):
    presentation.update_sidecar(tmp_path, color="#84cc16", hue=95.0)
    presentation.update_sidecar(tmp_path, tagline="finance · gmail")
    data = presentation.read_sidecar(tmp_path)
    assert data == {"color": "#84cc16", "hue": 95.0, "tagline": "finance · gmail"}


def test_registry_reads_color_toml_wins(tmp_path):
    d = tmp_path / "agents" / "helper"
    d.mkdir(parents=True)
    (d / "agent.toml").write_text('name = "Helper"\n', encoding="utf-8")
    (d / "presentation.json").write_text(json.dumps({"color": "#112233"}), encoding="utf-8")
    assert _registry(tmp_path).get("helper").color == "#112233"  # sidecar fills
    (d / "agent.toml").write_text('name = "Helper"\ncolor = "#aabbcc"\n', encoding="utf-8")
    assert _registry(tmp_path).get("helper").color == "#aabbcc"  # authored wins


def test_main_is_pinned_to_brand_lime(tmp_path):
    # main gets the brand lime even if a stale sidecar assigned it something else
    d = tmp_path / "agents" / "main"
    d.mkdir(parents=True)
    (d / "presentation.json").write_text(json.dumps({"color": "#51d654"}), encoding="utf-8")
    assert _registry(tmp_path).get("main").color == presentation.MAIN_COLOR
    # ...unless explicitly authored otherwise
    (d / "agent.toml").write_text('color = "#123456"\n', encoding="utf-8")
    assert _registry(tmp_path).get("main").color == "#123456"


def test_registry_create_scaffolds_and_loads(tmp_path):
    reg = _registry(tmp_path)
    spec = reg.create(
        "travel-planner",
        name="Travel Planner",
        description="plans trips",
        identity="You are a travel planner.",
    )
    assert spec.id == "travel-planner" and spec.name == "Travel Planner"
    assert spec.description == "plans trips"
    d = tmp_path / "agents" / "travel-planner"
    assert (d / "agent.toml").is_file() and (d / "IDENTITY.md").is_file()
    assert "travel planner" in spec.instructions.lower()  # IDENTITY loaded into bootstrap
    assert "travel-planner" in reg.list_ids()  # usable without restart

    import pytest

    with pytest.raises(ValueError):  # duplicate
        reg.create("travel-planner", name="Dup")
    with pytest.raises(ValueError):  # bad id
        reg.create("bad id!", name="x")


def test_gateway_agents_create(tmp_path):
    import asyncio

    from agent_runtime.presentation.gateway import Gateway

    reg = _registry(tmp_path)
    events = []

    class _WS:
        async def send(self, frame):
            events.append(frame)

    gw = Gateway(
        config=SimpleNamespace(
            state_dir=tmp_path / "state", agent_id="main", agent_name="jarvis", cost_efficiency=None
        ),
        service=None,
        registry=reg,
    )
    gw.clients = {_WS()}

    out = asyncio.run(gw._agents_create({"name": "Weather Bot", "description": "forecasts"}))
    assert out["created"] and out["agentId"] == "weather-bot"  # slug from the name
    assert "weather-bot" in reg.list_ids()
    assert any("agents.changed" in f for f in events)

    dup = asyncio.run(gw._agents_create({"name": "Weather Bot"}))
    assert not dup["created"] and "exists" in dup["error"]
    assert not asyncio.run(gw._agents_create({"name": ""}))["created"]  # needs a name

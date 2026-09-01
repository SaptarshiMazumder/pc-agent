"""models.list + per-kind model catalogs — the single model overview.

Covers: model-bearing tools carry a `model_kind`; the config catalogs split into text/vision/
image/embedding so an image tool's picker offers image models (not text); and models.list gives
the brain + every tool model with what it resolves to, its kind, and where it's set.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.presentation import gateway as gw


def test_image_catalog_has_image_models_not_text():
    labels = {o["value"] for o in gw.IMAGE_MODEL_CATALOG}
    assert "gemini/gemini-3-pro-image" in labels  # Nano Banana Pro
    assert "black-forest-labs/flux-1.1-pro" in labels  # FLUX
    assert "gemini/gemini-3.1-pro-preview" not in labels  # a TEXT model must never be offered here


def test_kind_catalogs_are_separated():
    cfg = SimpleNamespace(
        plugins={},
        model="gemini/gemini-3.1-pro-preview",
        model_catalog=None,
        model_fallbacks=[],
        cost_efficiency={},
    )
    cats = gw._kind_catalogs(cfg)
    assert set(cats) >= {"models", "text", "vision", "image-gen", "embedding"}
    img = {o["value"] for o in cats["image-gen"]}
    txt = {o["value"] for o in cats["text"]}
    # FLUX has an "unknown" provider prefix so it's never key-filtered — a stable image-only marker
    assert "black-forest-labs/flux-1.1-pro" in img  # image picker HAS image models
    assert "black-forest-labs/flux-1.1-pro" not in txt  # and the text picker does NOT
    assert "gemini/gemini-3-pro-image" not in txt  # no image model ever leaks into text
    assert cfg.model in txt  # the configured brain is always offered


def _models_payload_real():
    """models.list over the REAL config so build_catalog actually discovers the plugin tools."""
    from agent_runtime.config import load_config

    g = object.__new__(gw.Gateway)
    g.config = load_config()
    return g._models_list()


def test_models_list_reports_brain_and_tool_models_with_kind_and_key():
    out = _models_payload_real()
    by_id = {m["id"]: m for m in out["models"]}
    # the image tool: right KIND + the exact config path a client edits
    ga = by_id.get("figure-art.generate_artwork")
    assert ga is not None, "generate_artwork should appear as a model-bearing tool"
    assert ga["kind"] == "image-gen"
    assert ga["configKey"] == "plugins.figure-art.tools.generate_artwork.model"
    assert ga["resolved"]  # resolves to something (its config/default)
    # vision tools are tagged vision, not text
    assert by_id["vision.verify_figure"]["kind"] == "vision"
    # the payload carries the per-kind pickers
    assert "image-gen" in out["catalogs"] and "text" in out["catalogs"]


def test_kind_default_model_resolution():
    """A tool that names NO model gets the house default for its KIND; config overrides; a tool's own
    default_model wins over the kind default; a per-tool config wins over everything."""
    from agent_runtime.application.tool_models import kind_default_model, resolve_tool_model

    seed = SimpleNamespace(plugins={}, model_defaults={})
    # 1) declare only model_kind (default=None) -> the house per-kind SEED
    assert (
        resolve_tool_model(seed, "p", "t", default=None, kind="image-gen")
        == "gemini/gemini-3-pro-image"
    )
    assert (
        resolve_tool_model(seed, "p", "t", default=None, kind="vision")
        == "gemini/gemini-3.1-pro-preview"
    )
    # 2) a tool's OWN default_model wins over the kind default (verify's cheap judge stays cheap)
    assert (
        resolve_tool_model(seed, "p", "verify", default="gemini/gemini-2.5-flash", kind="vision")
        == "gemini/gemini-2.5-flash"
    )
    # 3) config.model_defaults OVERRIDES the seed per kind (config-first)
    over = SimpleNamespace(plugins={}, model_defaults={"image-gen": "my/custom"})
    assert resolve_tool_model(over, "p", "t", default=None, kind="image-gen") == "my/custom"
    # 4) a per-tool config still wins over everything
    pertool = SimpleNamespace(
        plugins={"p": {"tools": {"t": {"model": "per/tool"}}}}, model_defaults={"image-gen": "x"}
    )
    assert resolve_tool_model(pertool, "p", "t", default=None, kind="image-gen") == "per/tool"
    # 5) text has no house default -> None (so a text tool inherits the brain via the caller)
    assert kind_default_model(seed, "text") is None


def _models_payload(plugins, cost_efficiency=None):
    cfg = SimpleNamespace(
        plugins=plugins,
        model="gemini/gemini-3.1-pro-preview",
        model_catalog=None,
        model_fallbacks=[],
        cost_efficiency=cost_efficiency or {},
    )
    g = object.__new__(gw.Gateway)
    g.config = cfg
    return g._models_list()


def test_models_list_splits_the_brain_when_cost_efficiency_on():
    out = _models_payload(
        {},
        cost_efficiency={
            "enabled": True,
            "text_model": "deepseek/deepseek-v4-pro",
            "vision_model": "gemini/gemini-3.1-pro-preview",
        },
    )
    by_id = {m["id"]: m for m in out["models"]}
    assert out["costEfficiency"] is True
    assert by_id["brain-text"]["resolved"] == "deepseek/deepseek-v4-pro"
    assert by_id["brain-text"]["configKey"] == "cost_efficiency.text_model"
    assert by_id["brain-vision"]["resolved"] == "gemini/gemini-3.1-pro-preview"
    assert "brain" not in by_id  # the single-brain row is replaced by the split

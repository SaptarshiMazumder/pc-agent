"""Newer partner nodes nest their settings (`model.duration`); the price must still read them."""

from __future__ import annotations

import partner_pricing


def test_nested_duration_is_read():
    provider = {"default_seconds": 8}
    assert partner_pricing._seconds_for(provider, {"model.duration": 15}) == 15.0


def test_flat_duration_still_read():
    assert partner_pricing._seconds_for({"default_seconds": 8}, {"duration": "10s"}) == 10.0


def test_no_duration_is_the_default_not_zero():
    assert partner_pricing._seconds_for({"default_seconds": 8}, {"prompt": "a cat"}) == 8.0


def test_prompt_words_never_pick_the_tier():
    rates = {"medium": 13.42, "max": 214.7, "_default": 214.7}
    inputs = {"prompt": "a medium shot of a runner", "quality": "max"}
    assert partner_pricing._tier_for(rates, inputs) == ("max", 214.7)


def test_nested_quality_setting_still_picks_the_tier():
    rates = {"1080p": 29.54, "4k": 88.62, "_default": 88.62}
    inputs = {"prompt": "shot in 1080p", "model.resolution": "4k"}
    assert partner_pricing._tier_for(rates, inputs) == ("4k", 88.62)


def test_one_model_is_priced_for_the_job():
    table = {"credits_per_usd": 211, "markup": 1.3, "providers": [
        {"id": "bytedance", "unit": "per_second", "default_seconds": 5,
         "models": {"seedance-2-5": {"480p": 20.0, "720p": 30.0, "_default": 30.0}}}]}
    quote = partner_pricing.price_model("seedance-2-5", 6, "720p", table)
    assert quote.ok and quote.items[0].credits == 180.0
    assert round(quote.usd, 4) == round(180 * 1.3 / 211, 4)


def test_an_unknown_model_is_unpriced_not_free():
    quote = partner_pricing.price_model("no-such-model", 5, "", {"providers": []})
    assert not quote.ok and quote.unpriced == ["no-such-model"]

"""A read-only install directory must still be able to run.

The trigger, on a real machine: the engine installed per-machine into C:\\Program Files, and the
first message a user sent died with

    [Errno 13] Permission denied: 'C:\\Program Files\\agentd\\resources\\python\\Lib\\
    site-packages\\litellm\\litellm_core_utils\\tokenizers\\<uuid>.tmp'

litellm pins TIKTOKEN_CACHE_DIR to a directory inside its own package at import time, and
tiktoken writes every encoding it fetches there. Nothing in agentd asked for that; it is simply
what happens if nobody says otherwise. A per-machine install is a SUPPORTED mode (the per-agent
stub reads HKLM precisely so an enterprise deployment is found), so "install it somewhere else"
is not the fix — not caching into our own code is.
"""

import os
from pathlib import Path

from agent_runtime import runtime_paths


def test_the_tiktoken_cache_is_redirected_under_the_user_home(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTD_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CUSTOM_TIKTOKEN_CACHE_DIR", raising=False)

    runtime_paths.redirect_library_caches()

    target = Path(os.environ["CUSTOM_TIKTOKEN_CACHE_DIR"])
    assert target == tmp_path / "home" / "cache" / "tiktoken"
    # under the user home, NOT next to the installed code
    assert runtime_paths.PACKAGE_DIR not in target.parents


def test_an_operators_own_cache_location_is_left_alone(monkeypatch, tmp_path):
    """A container that bakes in a shared read-only cache has already answered this question."""
    monkeypatch.setenv("AGENTD_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CUSTOM_TIKTOKEN_CACHE_DIR", "/opt/shared/tiktoken")

    runtime_paths.redirect_library_caches()

    assert os.environ["CUSTOM_TIKTOKEN_CACHE_DIR"] == "/opt/shared/tiktoken"


def test_the_shipped_encodings_are_copied_so_the_first_run_needs_no_network(monkeypatch, tmp_path):
    """Redirecting the cache would otherwise cost the offline capability the bundled directory
    exists for: without seeding, a first token count has to reach openaipublic.blob.core.windows.net
    — the exact host a locked-down network blocks."""
    bundled = tmp_path / "pkg" / "litellm_core_utils" / "tokenizers"
    bundled.mkdir(parents=True)
    (bundled / "9b5ad71b2ce5302211f9c61530b329a4922fc6a4").write_bytes(b"ENCODING")
    (bundled / "anthropic_tokenizer.json").write_text("{}")  # litellm reads this from its own path
    (bundled / "__init__.py").write_text("")

    class Spec:
        origin = str(tmp_path / "pkg" / "__init__.py")

    monkeypatch.setattr("importlib.util.find_spec", lambda name: Spec if name == "litellm" else None)

    target = tmp_path / "cache" / "tiktoken"
    runtime_paths._seed_tiktoken_cache(target)  # noqa: SLF001 — the seeding IS the unit here

    assert (target / "9b5ad71b2ce5302211f9c61530b329a4922fc6a4").read_bytes() == b"ENCODING"
    # only the hash-named blobs: the package's own files are not cache entries
    assert sorted(p.name for p in target.iterdir()) == ["9b5ad71b2ce5302211f9c61530b329a4922fc6a4"]


def test_a_cache_that_already_exists_is_never_overwritten(monkeypatch, tmp_path):
    """It may be in use by a running daemon, and it is newer than anything we would copy."""
    target = tmp_path / "cache" / "tiktoken"
    target.mkdir(parents=True)
    (target / "live").write_bytes(b"IN USE")

    def explode(name):
        raise AssertionError("must not even look for litellm when the cache is already there")

    monkeypatch.setattr("importlib.util.find_spec", explode)
    runtime_paths._seed_tiktoken_cache(target)  # noqa: SLF001

    assert (target / "live").read_bytes() == b"IN USE"


def test_seeding_survives_litellm_being_absent(monkeypatch, tmp_path):
    """Best-effort: no litellm means no seed, not a crash on import of agent_runtime."""
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    runtime_paths._seed_tiktoken_cache(tmp_path / "cache" / "tiktoken")  # noqa: SLF001

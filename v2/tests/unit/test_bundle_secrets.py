"""What a package must never contain: the credential its author happened to be using.

THE SHAPE OF THE RISK. An agent's own config file — `agent.config.json` — holds two different
kinds of thing in one place, because one file is what was asked for:

  * the AUTHOR's choices about how the agent runs, which are meant to travel
  * the values whoever is running it typed into its settings page, one of which may be their
    API key

The author is also the first person to run it, so by the time they publish, their own key is
sitting in the file that ships. Nothing about that is unusual; it is the normal way an agent gets
built. So the split has to happen at the boundary the credential must not cross, and it has to
happen without the author remembering anything.

TWO MECHANISMS, NOT ONE. The packer strips secret values on the way in, and then reads the
finished package back and refuses to hand it over if any survived. That second half is the point:
"the stripper is correct" is a hard thing to be sure of, and "this package is clean" is an easy
one. A filter you have to trust is what made a single config file the risky option.

WHAT COUNTS AS A SECRET is the agent's own declaration — `[[settings]] kind = "secret"` — so an
author who adds a credential field gets it stripped without anybody updating the packer.
"""

import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.cli.commands.bundle import _pack_agent_dir
from agent_runtime.domain.agent_config import secret_values_present, strip_secret_settings

AGENT_TOML = """name = "Comfy Smith"
version = "1.0.0"

[[settings]]
key  = "COMFY_URL"
kind = "url"

[[settings]]
key  = "COMFY_TOKEN"
kind = "secret"
"""


def _agent(tmp_path: Path, config: dict | str | None) -> Path:
    d = tmp_path / "agents" / "comfy-smith"
    d.mkdir(parents=True)
    (d / "agent.toml").write_text(AGENT_TOML, encoding="utf-8")
    (d / "IDENTITY.md").write_text("drives a remote ComfyUI", encoding="utf-8")
    if config is not None:
        body = config if isinstance(config, str) else json.dumps(config)
        (d / "agent.config.json").write_text(body, encoding="utf-8")
    return d


def _packed_config(pkg: Path) -> dict:
    with zipfile.ZipFile(pkg) as z:
        return json.loads(z.read("agent/agent.config.json").decode("utf-8"))


# --- the pure rule ----------------------------------------------------------


def test_only_the_declared_secrets_are_stripped():
    """A key the agent never declared is left alone. Nothing said it was a credential, and
    silently deleting an author's value would be worse than the thing this prevents."""
    authored = {"model": "m", "settings": {"COMFY_URL": "https://x", "NOTE": "keep me"}}
    out, removed = strip_secret_settings(authored, {"COMFY_TOKEN"})
    assert removed == []
    assert out["settings"] == {"COMFY_URL": "https://x", "NOTE": "keep me"}


def test_the_authors_run_settings_are_untouched():
    """Stripping is about credentials, not about the author's choices. The whole point of the
    file is that `model` travels."""
    authored = {"model": "gemini/x", "user_editable": False, "settings": {"T": "sk-live"}}
    out, _ = strip_secret_settings(authored, {"T"})
    assert out["model"] == "gemini/x" and out["user_editable"] is False


# --- packing ----------------------------------------------------------------


def test_a_secret_value_never_reaches_the_package(tmp_path):
    """THE ONE THAT MATTERS. The author filled in their own token while building; publishing must
    not upload it."""
    agent = _agent(
        tmp_path, {"model": "m", "settings": {"COMFY_URL": "https://pod", "COMFY_TOKEN": "sk-live"}}
    )
    pkg = _pack_agent_dir(agent, tmp_path / "out")

    packed = _packed_config(pkg)
    assert "COMFY_TOKEN" not in packed["settings"], "a declared secret shipped"
    # ...and the non-secret half still travels, which is why the file exists at all.
    assert packed["settings"]["COMFY_URL"] == "https://pod"
    assert packed["model"] == "m"


def test_the_authors_own_file_is_not_rewritten(tmp_path):
    """Packing that edited your source would be a surprise. What has to be clean is the artifact —
    the same rule the vendored-SDK substitution follows."""
    agent = _agent(tmp_path, {"settings": {"COMFY_TOKEN": "sk-live"}})
    _pack_agent_dir(agent, tmp_path / "out")

    on_disk = json.loads((agent / "agent.config.json").read_text(encoding="utf-8"))
    assert on_disk["settings"]["COMFY_TOKEN"] == "sk-live"


def test_an_agent_with_no_config_packs_as_before(tmp_path):
    """Every agent built before this existed ships none. If that changed, adding the feature would
    have altered every package."""
    agent = _agent(tmp_path, None)
    pkg = _pack_agent_dir(agent, tmp_path / "out")
    with zipfile.ZipFile(pkg) as z:
        assert "agent/agent.config.json" not in z.namelist()


def test_an_unreadable_config_packs_empty_rather_than_verbatim(tmp_path):
    """The one outcome that could ship a secret is copying a file we could not parse. It is
    replaced with an empty one instead — `validate_agent` is where a malformed config gets
    reported, and the packer's job here is only to not leak."""
    agent = _agent(tmp_path, "{ this is not json")
    pkg = _pack_agent_dir(agent, tmp_path / "out")
    assert _packed_config(pkg) == {}


# --- the verification, which is the actual guarantee ------------------------


def test_the_packer_checks_its_own_output(tmp_path, monkeypatch):
    """THE SECOND MECHANISM. If the stripper ever fails — a bug, a refactor, a new code path that
    writes the file another way — the package is read back, the leak is caught, the package is
    DELETED and the pack fails loudly. Nothing gets published.

    Simulated by breaking the stripper on purpose, because a test that only exercises the working
    path proves the working path."""
    import agent_runtime.infrastructure.marketplace.bundle_io as bundle_io

    monkeypatch.setattr(bundle_io, "strip_secret_settings", lambda authored, keys: (authored, []))

    agent = _agent(tmp_path, {"settings": {"COMFY_TOKEN": "sk-live"}})
    with pytest.raises(ValueError) as caught:
        _pack_agent_dir(agent, tmp_path / "out")

    said = str(caught.value)
    assert "COMFY_TOKEN" in said
    assert "REFUSING" in said
    # and it did not leave the leaking package lying around for somebody to publish by hand
    assert not list((tmp_path / "out").glob("*.agentpkg")), "the bad package was left on disk"


def test_verification_reports_what_is_still_present():
    """The check the packer runs, on its own. Kept separate because a verification that cannot say
    WHAT should have gone cannot fail usefully."""
    assert secret_values_present({"settings": {"T": "sk"}}, {"T"}) == ["T"]
    assert secret_values_present({"settings": {"T": "sk"}}, set()) == []
    assert secret_values_present({}, {"T"}) == []

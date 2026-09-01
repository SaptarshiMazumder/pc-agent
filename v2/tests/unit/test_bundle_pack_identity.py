"""`agentd bundle pack` — where a bundle's identity comes from.

THE BUG THIS PINS: the CLI packer read identity only from an optional ``bundle.toml`` and fell
back to the directory name and ``"1.0.0"``. It never read ``agent.toml``. So an agent declaring
``version = "0.2.0"`` shipped as 1.0.0 — and because installs supersede BY VERSION, every later
release silently failed to replace the first copy on a buyer's machine.

It was found and fixed once, in agent-authoring's ``BundleDefaults`` (the chat path). The CLI kept
the bug, and the CLI is what ``gen-app-flavor.mjs`` calls to build the payload for a per-agent
installer — so the shipping path was the broken one. These tests exist because a silent no-op
update is invisible until a customer reports it, which is far too late.

The precedence chain is the contract, and it must be IDENTICAL in both packers:

    explicit argument  >  bundle.toml  >  agent.toml  >  fallback

``bundle.toml`` stays on top because it is the publisher-facing file (publisher, entitlement, a
bundle id that differs from the agent id). It just stops being the ONLY source of what
``agent.toml`` already states.
"""

import sys
import tomllib
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.cli.commands.bundle import _pack_agent_dir


def _agent(tmp_path: Path, agent_toml: str = "", bundle_toml: str = "", name="game-master") -> Path:
    d = tmp_path / "agents" / name
    d.mkdir(parents=True)
    if agent_toml:
        (d / "agent.toml").write_text(agent_toml, encoding="utf-8")
    if bundle_toml:
        (d / "bundle.toml").write_text(bundle_toml, encoding="utf-8")
    (d / "IDENTITY.md").write_text("a test agent", encoding="utf-8")
    return d


def _manifest(pkg: Path) -> dict:
    """The bundle.toml written INSIDE the zip — the copy an installer actually reads."""
    with zipfile.ZipFile(pkg) as z:
        return tomllib.loads(z.read("bundle.toml").decode("utf-8"))["bundle"]


# --- version: the field the bug was about -----------------------------------


def test_agent_toml_version_ships(tmp_path):
    """THE regression. The version the author bumped is the version that ships."""
    agent = _agent(tmp_path, 'name = "Game Master"\nversion = "0.2.0"\n')
    pkg = _pack_agent_dir(agent, tmp_path / "out")
    assert _manifest(pkg)["version"] == "0.2.0"
    # the FILENAME carries it too: gen-app-flavor and the installer both key off this
    assert pkg.name == "game-master-0.2.0.agentpkg"


def test_bundle_toml_outranks_agent_toml(tmp_path):
    """bundle.toml is publisher-facing, so a publisher who pins a version keeps winning."""
    agent = _agent(
        tmp_path,
        'version = "0.2.0"\n',
        '[bundle]\nversion = "9.9.9"\n',
    )
    assert _manifest(_pack_agent_dir(agent, tmp_path / "out"))["version"] == "9.9.9"


def test_explicit_argument_outranks_everything(tmp_path):
    """`--version` is a deliberate one-off build; nothing on disk may override the operator."""
    agent = _agent(tmp_path, 'version = "0.2.0"\n', '[bundle]\nversion = "9.9.9"\n')
    pkg = _pack_agent_dir(agent, tmp_path / "out", version="1.2.3")
    assert _manifest(pkg)["version"] == "1.2.3"


def test_fallback_when_nothing_declares_a_version(tmp_path):
    """Unchanged behavior — the fix adds a tier, it does not remove the floor."""
    agent = _agent(tmp_path, 'name = "Game Master"\n')
    assert _manifest(_pack_agent_dir(agent, tmp_path / "out"))["version"] == "1.0.0"


@pytest.mark.parametrize("declared", ['version = ""\n', 'version = "   "\n'])
def test_blank_version_is_not_a_declaration(tmp_path, declared):
    """An empty string must fall THROUGH, not ship as a bundle with no version."""
    agent = _agent(tmp_path, declared)
    assert _manifest(_pack_agent_dir(agent, tmp_path / "out"))["version"] == "1.0.0"


# --- the other identity fields ----------------------------------------------


def test_name_and_description_come_from_agent_toml(tmp_path):
    """Same precedence, same reason: agent.toml already states these, so a publisher should not
    have to restate them in a second file just to get a correctly labelled store card."""
    agent = _agent(
        tmp_path,
        'name = "Game Master"\nversion = "0.2.0"\ndescription = "rolls dice"\n',
    )
    m = _manifest(_pack_agent_dir(agent, tmp_path / "out"))
    assert m["name"] == "Game Master"
    assert m["description"] == "rolls dice"


def test_bundle_id_still_prefers_bundle_toml_then_directory_name(tmp_path):
    """Bundle id is deliberately NOT taken from agent.toml: the installer derives the agent id
    from the directory it unpacks, and a bundle id that differs is a publisher decision."""
    plain = _agent(tmp_path / "a", 'version = "0.2.0"\n')
    assert _manifest(_pack_agent_dir(plain, tmp_path / "o1"))["id"] == "game-master"

    renamed = _agent(tmp_path / "b", 'version = "0.2.0"\n', '[bundle]\nid = "gm-pro"\n')
    assert _manifest(_pack_agent_dir(renamed, tmp_path / "o2"))["id"] == "gm-pro"


def test_app_icon_is_not_the_bundle_icon(tmp_path):
    """`icon` under [app] is an INSTALLER artifact (a path to an .ico); the bundle's icon is a
    store-card glyph name. Taking one for the other would put a file path in the store UI."""
    agent = _agent(tmp_path, 'version = "0.2.0"\n\n[app]\nicon = "icon.ico"\n')
    # `.get` because an undeclared icon is OMITTED from the written manifest, not blanked —
    # either shape is fine, what matters is that "icon.ico" never appears.
    assert _manifest(_pack_agent_dir(agent, tmp_path / "out")).get("icon", "") == ""


# --- robustness: packing must not require agent.toml to be readable ----------


@pytest.mark.parametrize(
    ("agent_toml", "why"),
    [("", "no agent.toml at all"), ("this is not = valid toml [[[\n", "unparseable agent.toml")],
)
def test_packing_survives_a_missing_or_broken_agent_toml(tmp_path, agent_toml, why):
    """A directory is still packable; it just has fewer defaults to draw on. Refusing here would
    make `bundle pack` useless for anything that is not a fully-formed agent."""
    agent = _agent(tmp_path, agent_toml)
    m = _manifest(_pack_agent_dir(agent, tmp_path / "out"))
    assert m["version"] == "1.0.0", why
    assert m["id"] == "game-master", why

"""Packaging refuses an agent whose own tools cannot run for the person installing it.

The sandbox findings are advisory while you are authoring, and that is right: on your machine
your tools are trusted and nothing is broken. Packaging is the moment that stops being true. A
``.agentpkg`` exists to be installed, and installing records the agent in the buyer's ledger —
from then on its private tools are untrusted over there.

So the same warning means two different things depending on when you read it, and only one of
them is survivable. `validate_agent` keeps warning; `package_agent` refuses.

The motivating case is real: a ComfyUI agent built here shipped a tool that revealed files with
``subprocess.Popen(["explorer.exe", ...])``. Perfect for its author. A hard denial for everyone
who installs it, and nothing stopped it being packaged.

WHICH codes block is the RULEBOOK's one decision (domain/rulebook.py, ``blockers(PACK)``) —
this test pins that the gate obeys the table. Every entry there has to be a shape with no
innocent reading. `import httpx` is one. A bare ``https://`` in a docstring is not — that stays
advisory, because a gate that fires on a comment is a gate authors learn to route around, and
then it guards nothing at all.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_authoring.application.package_agent_service import PackageAgentService
from agent_authoring.domain.finding import ERROR, WARN, Finding
from agent_authoring.domain.report import Report
from agent_authoring.domain.rulebook import PACK, blockers

_PACK_BLOCKERS = blockers(PACK)


class _Reader:
    def __init__(self, agent_dir):
        self._dir = agent_dir

    def agent_dir(self, agent_id):
        return self._dir

    def known_ids(self):
        return ["demo"]

    def raw(self, agent_dir):
        return {}


class _Validator:
    def __init__(self, findings):
        self._findings = findings

    def validate(self, agent_id):
        return Report(agent_id=agent_id, findings=list(self._findings))


class _Packer:
    """Matches AgentPacker's real surface — the service calls all four."""

    packed = False

    def __init__(self, out):
        self._out = out

    def default_out_dir(self):
        return self._out

    def bundle_table(self, agent_dir):
        return {}

    def private_plugin_ids(self, agent_dir):
        return ["kit"]

    def pack(self, agent_dir, out_dir, manifest):
        _Packer.packed = True
        path = Path(out_dir) / "demo-1.0.0.agentpkg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"pkg")
        return path, "deadbeef"


class _Manifest:
    """Only what the service reads: id, version, plugins (for its summary line)."""

    def __init__(self, agent_id, version):
        self.id, self.version, self.plugins = agent_id, version, []


class _Defaults:
    def manifest(self, **kw):
        return _Manifest(kw.get("agent_id", "demo"), kw.get("version") or "1.0.0")


def _service(tmp_path, findings):
    _Packer.packed = False
    return PackageAgentService(
        _Reader(tmp_path), _Packer(tmp_path / "dist"), _Defaults(), _Validator(findings)
    )


def _warn(code):
    return Finding(level=WARN, code=code, message=f"{code} happened", path="plugins/kit/",
                   fix="do it the other way")


# ── the refusal ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("code", sorted(_PACK_BLOCKERS))
def test_each_dead_on_arrival_shape_blocks_packaging(tmp_path, code):
    res = _service(tmp_path, [_warn(code)]).package("demo")
    assert res.ok is False
    assert _Packer.packed is False, "it must refuse BEFORE writing a package"


def test_the_refusal_explains_why_it_works_here_and_not_there(tmp_path):
    """An author whose agent validates OK and then fails to package needs to know why the same
    code is fine on this machine — otherwise the refusal reads as a bug in the packer."""
    res = _service(tmp_path, [_warn("UNTRUSTED_WANTS_SPAWN")]).package("demo")
    assert "installs this" in res.message
    assert "UNTRUSTED_WANTS_SPAWN" in res.message
    assert "do it the other way" in res.message, "carry the finding's own fix through"


# ── and stays quiet otherwise ───────────────────────────────────────────────
def test_a_clean_agent_still_packages(tmp_path):
    assert _service(tmp_path, []).package("demo").ok is True
    assert _Packer.packed is True


def test_an_advisory_finding_does_not_block(tmp_path):
    """UNTRUSTED_MAYBE_NETWORK is the URL-in-a-docstring case — deliberately fuzzy, so it can
    warn but must never stop a release."""
    assert "UNTRUSTED_MAYBE_NETWORK" not in _PACK_BLOCKERS
    assert _service(tmp_path, [_warn("UNTRUSTED_MAYBE_NETWORK")]).package("demo").ok is True


def test_an_unrelated_warning_does_not_block(tmp_path):
    assert _service(tmp_path, [_warn("COMPANION_TOOL_MISSING")]).package("demo").ok is True


def test_a_real_error_still_blocks(tmp_path):
    """The original gate is untouched: an ERROR fails validation outright, and that refusal
    says 'validation failed' rather than the sandbox message."""
    bad = Finding(level=ERROR, code="NO_IDENTITY", message="no IDENTITY.md", path="")
    res = _service(tmp_path, [bad]).package("demo")
    assert res.ok is False
    assert "validation failed" in res.message


# ── every blocking code is a code the rules can actually emit ───────────────
def test_the_blocking_list_names_real_findings():
    """A code that no rule emits is a gate that never fires — the quiet kind of dead check."""
    domain = (Path(__file__).resolve().parents[2] / "agents" / "agent-builder" / "plugins"
              / "agent-authoring" / "agent_authoring" / "domain")
    src = "\n".join(
        (domain / name).read_text(encoding="utf-8")
        for name in (
            "sandbox_rules.py",
            "portability_rules.py",
            "packageability_rules.py",
            "declaration_rules.py",
        )
    )
    for code in _PACK_BLOCKERS:
        assert f'code="{code}"' in src, f"{code} is blocked but nothing emits it"

"""What an APP WINDOW may do — the boundary between a page and the daemon.

An agent app is a web page. It may have shipped inside someone else's downloaded package, and
/apps/ pages have no CSP, so anything it can read it can POST anywhere. These tests pin the two
rules that follow from that:

  A. config.get REDACTS its secret-bearing fields for an INSTALLED agent's window, while a
     locally-authored agent (and every host connection) still gets the full surface. Writing a
     key is always allowed — that is BYOK; reading one back is what leaks.

  B. A scoped request's `agentId` is overwritten with the connection's own agent, so no window
     can read or act as another. Agent Builder is the single hardcoded exception, and only for
     READ methods.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json

import pytest

from agent_runtime.presentation.gateway import (
    APP_SCOPED_METHODS,
    CROSS_AGENT_READS,
    Gateway,
)

REDACTED = ("envValues", "raw", "path", "envPath")
KEPT = ("values", "env", "catalogs", "providerKeys")


def _payload():
    """A config.get payload shaped like the real one."""
    return {
        "path": "C:/x/agentd.config.json",
        "envPath": "C:/x/.env",
        "raw": '{"model": "x", "note": "a key I pasted by hand"}',
        "values": {"model": "x"},
        "env": {"ANTHROPIC_API_KEY": True},
        "envValues": {"ANTHROPIC_API_KEY": "sk-ant-SECRET"},
        "providerKeys": ["ANTHROPIC_API_KEY"],
        "catalogs": {},
    }


def _gateway(tmp_path, installed=()):
    gw = Gateway.__new__(Gateway)  # no daemon: only the pure policy methods are exercised
    gw.config = type("C", (), {"state_dir": str(tmp_path)})()
    ledger = tmp_path / "installed_bundles.json"
    ledger.write_text(
        json.dumps({"bundles": [{"id": i, "version": "1.0.0"} for i in installed]}),
        encoding="utf-8",
    )
    return gw


# --- A. the settings surface -------------------------------------------------
def test_installed_agent_never_sees_a_key(tmp_path):
    gw = _gateway(tmp_path, installed=["downloaded-agent"])
    out = gw._redact_for_installed_agent(_payload(), "downloaded-agent")
    for field in REDACTED:
        assert field not in out, f"{field} leaked to an installed agent"
    assert "sk-ant-SECRET" not in json.dumps(out)


def test_installed_agent_still_gets_a_usable_settings_page(tmp_path):
    """Redaction must not break BYOK: the page still knows WHICH keys exist and can save one."""
    gw = _gateway(tmp_path, installed=["downloaded-agent"])
    out = gw._redact_for_installed_agent(_payload(), "downloaded-agent")
    for field in KEPT:
        assert field in out
    assert out["env"] == {"ANTHROPIC_API_KEY": True}  # presence, not value


def test_local_agent_gets_the_full_surface(tmp_path):
    """An agent that shipped with the product or was authored here is not a stranger."""
    gw = _gateway(tmp_path, installed=["someone-elses-agent"])
    out = gw._redact_for_installed_agent(_payload(), "agent-builder")
    assert out == _payload()


def test_host_connection_is_never_redacted(tmp_path):
    """scope=None is JARVIS itself — the reveal toggle must keep working."""
    gw = _gateway(tmp_path, installed=["agent-builder"])
    assert gw._redact_for_installed_agent(_payload(), None) == _payload()


def test_unreadable_ledger_fails_closed(tmp_path):
    gw = _gateway(tmp_path)
    (tmp_path / "installed_bundles.json").write_text("{ not json", encoding="utf-8")
    assert gw._is_installed_agent("anything") is True
    out = gw._redact_for_installed_agent(_payload(), "anything")
    assert "envValues" not in out


def test_missing_ledger_is_not_a_failure(tmp_path):
    """A fresh machine has installed nothing — that must not redact every agent."""
    gw = Gateway.__new__(Gateway)
    gw.config = type("C", (), {"state_dir": str(tmp_path / "empty")})()
    assert gw._is_installed_agent("agent-builder") is False


def test_config_methods_are_app_callable():
    assert {"config.get", "config.set"} <= APP_SCOPED_METHODS


# --- B. the cross-agent boundary ---------------------------------------------
def test_only_the_builders_may_cross():
    """A PINNED LIST, deliberately. Crossing the app-scope boundary is the one exception to "an
    app can never act as another agent", so growing this set must be a conscious edit here rather
    than something a change to the gateway does quietly on its way past.

    Cloud Agent Builder joined it because authoring an agent means reading it, and cabbie is the
    only builder the web has. It gets STRUCTURALLY less than agent-builder does — the sibling
    tests below hold the line on what any of them may do."""
    assert set(CROSS_AGENT_READS) == {"agent-builder", "cloud-agent-builder"}


def test_the_hosted_builder_cannot_read_another_agents_transcripts():
    """The reason cabbie's grant is narrower, pinned so it cannot widen by accident. agent-builder
    keeps sessions because it is desktop-only, where the caller is the machine's owner reading
    their own machine; cabbie runs on a shared daemon where the agent beside it is a stranger's,
    and authoring never needs to read what somebody said to it."""
    assert "sessions.list" not in CROSS_AGENT_READS["cloud-agent-builder"]
    assert "sessions.history" not in CROSS_AGENT_READS["cloud-agent-builder"]


@pytest.mark.parametrize(
    "method", ["chat.send", "sessions.delete", "workspace.delete", "workspace.upload",
               "workspace.mkdir"]
)
def test_no_write_method_can_ever_cross(method):
    """Reading ABOUT another agent is the feature. Acting or destroying AS one is not —
    including for Agent Builder."""
    for allowed in CROSS_AGENT_READS.values():
        assert method not in allowed


def test_cross_agent_reads_are_all_real_app_methods():
    """A typo here would silently grant nothing (or, worse, look like it granted something)."""
    for agent, methods in CROSS_AGENT_READS.items():
        assert methods <= APP_SCOPED_METHODS, f"{agent} names a non-app method"


def test_the_file_browsing_method_is_granted():
    """The whole point: Agent Builder's window can list the agent it just built."""
    assert "workspace.list" in CROSS_AGENT_READS["agent-builder"]

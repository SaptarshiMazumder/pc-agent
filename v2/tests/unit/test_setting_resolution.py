"""Which environment variable a `${NAME}` placeholder actually reads.

A declared setting is stored under the agent's own prefix, so two agents can hold two accounts
for the same service. That is only true if EVERY consumer resolves it the same way, so this file
pins the rule itself and then pins both substitution sites against it:

  net.outbound._resolved            the author's own machine, in-process
  SandboxFetchBroker._substituted   the same plugin with the sandbox switched on

Those two disagreeing is the expensive bug: the plugin works, you turn on the sandbox, it 401s,
and the sandbox gets blamed for a week.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.application.run_context import (
    RunContext,
    current_setting_env,
    set_run_context,
)
from agent_runtime.domain.agent import resolve_setting_env, setting_env_name
from agent_runtime.infrastructure.net.outbound import _resolved
from agent_runtime.infrastructure.tools.sandbox.capabilities import CapabilityGrant
from agent_runtime.infrastructure.tools.sandbox.fetch_broker import SandboxFetchBroker


# ── the rule ────────────────────────────────────────────────────────────────
def test_the_prefix_is_the_agent_id_verbatim():
    """Not upper-cased, not slugged: `aws-provisioner` and `aws_provisioner` are both valid ids,
    and folding them together would recreate the collision the prefix exists to prevent."""
    assert setting_env_name("aws-provisioner", "AWS_ACCESS_KEY_ID") == "aws-provisioner__AWS_ACCESS_KEY_ID"
    assert setting_env_name("aws_provisioner", "AWS_ACCESS_KEY_ID") == "aws_provisioner__AWS_ACCESS_KEY_ID"


def test_a_declared_name_is_private_and_an_undeclared_one_is_the_machines():
    declared = ("AWS_ACCESS_KEY_ID",)
    assert resolve_setting_env("AWS_ACCESS_KEY_ID", "trader", declared) == "trader__AWS_ACCESS_KEY_ID"
    # FAL_KEY, GOOGLE_OAUTH_CLIENT_ID, a provider key: machine-wide, exactly as before.
    assert resolve_setting_env("FAL_KEY", "trader", declared) == "FAL_KEY"


def test_no_agent_means_no_prefix():
    """A channel, a cron boot, anything outside a run reads the machine-wide variable."""
    assert resolve_setting_env("FAL_KEY", "", ()) == "FAL_KEY"


def test_the_rule_is_a_lookup_not_a_fallback_chain():
    """It never tries the prefixed name and then settles for the bare one — that is how an agent
    with no key of its own would silently run on the daemon's credentials and report success."""
    assert resolve_setting_env("AWS_ACCESS_KEY_ID", "trader", ("AWS_ACCESS_KEY_ID",)) != "AWS_ACCESS_KEY_ID"


# ── the run context ─────────────────────────────────────────────────────────
def _run_as(agent_id: str, *declared: str) -> None:
    set_run_context(RunContext(agent_id=agent_id, session_key="s", mode="interactive", settings=declared))


def test_current_setting_env_follows_the_running_agent():
    _run_as("trader", "ACME_API_KEY")
    assert current_setting_env("ACME_API_KEY") == "trader__ACME_API_KEY"
    _run_as("other", "ACME_API_KEY")
    assert current_setting_env("ACME_API_KEY") == "other__ACME_API_KEY"


def test_outside_a_run_the_bare_name_is_correct():
    set_run_context(None)  # type: ignore[arg-type]
    assert current_setting_env("ACME_API_KEY") == "ACME_API_KEY"


# ── both substitution sites, on the same input ──────────────────────────────
def _serve(broker, **request):
    return asyncio.run(broker.serve({"t": "fetch_request", "id": "f1", **request}))


class _Cfg:
    plugins: dict = {}
    sandbox_fetch_limits: dict = {}


def _broker(declared):
    return SandboxFetchBroker(
        _Cfg(),
        plugin_id="acme",
        tool_name="call_acme",
        grant=CapabilityGrant(net_allowlist=("api.acme.com",)),
        declared_secrets=declared,
    )


def test_the_declaring_agent_gets_its_own_key_in_process(monkeypatch):
    monkeypatch.setenv("trader__ACME_API_KEY", "sk-traders-own")
    monkeypatch.setenv("ACME_API_KEY", "sk-the-daemons")
    _run_as("trader", "ACME_API_KEY")
    assert _resolved("Bearer ${ACME_API_KEY}") == "Bearer sk-traders-own"


def test_an_undeclared_name_still_reads_the_machine_wide_value(monkeypatch):
    """Every plugin credential that exists today — FAL_KEY, GOOGLE_OAUTH_*, LINE_* — is one of
    these. The retrofit must not move them."""
    monkeypatch.setenv("FAL_KEY", "fal-machine-wide")
    _run_as("trader", "ACME_API_KEY")
    assert _resolved("Key ${FAL_KEY}") == "Key fal-machine-wide"


def test_an_agent_that_declares_nothing_is_unaffected(monkeypatch):
    monkeypatch.setenv("ACME_API_KEY", "sk-the-daemons")
    _run_as("plain")
    assert _resolved("Bearer ${ACME_API_KEY}") == "Bearer sk-the-daemons"


def test_the_agents_own_key_is_not_the_daemons(monkeypatch):
    """The AWS case: the agent declares the name, the user has not filled it in, and the daemon
    happens to export one. The placeholder must NOT resolve to the daemon's."""
    monkeypatch.delenv("trader__ACME_API_KEY", raising=False)
    monkeypatch.setenv("ACME_API_KEY", "sk-the-daemons")
    _run_as("trader", "ACME_API_KEY")
    assert _resolved("Bearer ${ACME_API_KEY}") == "Bearer ${ACME_API_KEY}"  # left literal, visibly wrong


def test_the_sandboxed_path_resolves_identically(monkeypatch):
    """Same agent, same placeholder, sandbox on: the header must come out the same."""
    monkeypatch.setenv("trader__ACME_API_KEY", "sk-traders-own")
    monkeypatch.setenv("ACME_API_KEY", "sk-the-daemons")
    _run_as("trader", "ACME_API_KEY")
    header = {"Authorization": "Bearer ${ACME_API_KEY}"}
    assert _broker(("ACME_API_KEY",))._substituted(header) == {"Authorization": "Bearer sk-traders-own"}
    assert _resolved(header["Authorization"]) == "Bearer sk-traders-own"


def test_the_sandboxed_path_also_refuses_the_daemons_key(monkeypatch):
    """The unfilled case, sandboxed. Left as the literal placeholder — a debuggable 401 rather
    than a request that quietly went out on somebody else's credentials."""
    monkeypatch.delenv("trader__ACME_API_KEY", raising=False)
    monkeypatch.setenv("ACME_API_KEY", "sk-the-daemons")
    _run_as("trader", "ACME_API_KEY")
    out = _broker(("ACME_API_KEY",))._substituted({"Authorization": "Bearer ${ACME_API_KEY}"})
    assert out == {"Authorization": "Bearer ${ACME_API_KEY}"}


def test_the_two_paths_agree_on_an_undeclared_name(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "fal-machine-wide")
    _run_as("trader", "ACME_API_KEY")
    broker = _broker(("FAL_KEY",))
    assert broker._substituted({"X": "${FAL_KEY}"}) == {"X": "fal-machine-wide"}
    assert _resolved("${FAL_KEY}") == "fal-machine-wide"

"""What `validate_agent` catches in the three declaration blocks.

Every case here is a mistake that WORKS ON THE AUTHOR'S MACHINE and fails on somebody else's.
That is the whole category, and it is why these checks are worth having at all: the author has
the key in their .env, the server already added to their config, the app registered under their
account. Nothing they can see is wrong.

Written last, on purpose, against finished behaviour — a validator written alongside a moving
rule validates a version of the rule that no longer exists.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[2]
        / "agents"
        / "agent-builder"
        / "plugins"
        / "agent-authoring"
    ),
)

from agent_runtime.presentation.gateway import PROVIDER_ENV_KEYS

from agent_authoring.domain.declaration_rules import DeclarationRules

#: The REAL list the daemon shares, injected exactly as the composition root does — so this
#: pins the shipped behaviour rather than a convenient stand-in.
RULES = DeclarationRules(provider_keys=PROVIDER_ENV_KEYS)


def _codes(raw: dict, sources: dict | None = None) -> set:
    return {f.code for f in RULES.check(None, raw, [], sources or {})}


def _level(raw: dict, code: str) -> str:
    return next(f.level for f in RULES.check(None, raw, [], {}) if f.code == code)


WIRED = {
    "settings": [{"key": "ACME_API_KEY"}],
    "mcp": [{"name": "acme", "url": "https://acme/mcp", "headers": {"A": "Bearer ${ACME_API_KEY}"}}],
    "capabilities": {"mcp_workshop": False},
}


# ── a correct agent is quiet ────────────────────────────────────────────────
def test_an_agent_that_declares_nothing_gets_nothing():
    """Most agents. A rule that fires on them is a rule people learn to ignore."""
    assert _codes({"name": "Plain"}) == set()


def test_a_correctly_wired_agent_raises_only_the_local_only_note():
    assert _codes(WIRED) == {"DECLARATIONS_ARE_LOCAL_ONLY"}
    assert _level(WIRED, "DECLARATIONS_ARE_LOCAL_ONLY") == "info"


# ── the one that costs money ────────────────────────────────────────────────
def test_a_real_key_pasted_into_agent_toml_is_an_error():
    """agent.toml SHIPS. This is the mistake that sends the author's own credential to every
    person who installs the agent."""
    raw = {
        "settings": [{"key": "ACME_API_KEY"}],
        "mcp": [{"name": "a", "url": "https://x", "headers": {"A": "Bearer sk-live-ABCDEFGH12345678"}}],
    }
    assert "CREDENTIAL_IN_AGENT_TOML" in _codes(raw)
    assert _level(raw, "CREDENTIAL_IN_AGENT_TOML") == "error"


def test_it_finds_a_key_nested_anywhere_not_only_in_the_blocks_it_knows():
    raw = {"settings": [{"key": "K"}], "plugins": {"thing": {"tools": {"t": {"token": "AKIAIOSFODNN7EXAMPLE"}}}}}
    assert "CREDENTIAL_IN_AGENT_TOML" in _codes(raw)


def test_a_placeholder_is_not_a_credential():
    """The correct spelling must never trip the check that exists to enforce it."""
    assert "CREDENTIAL_IN_AGENT_TOML" not in _codes(WIRED)


def test_ordinary_prose_is_not_a_credential():
    raw = {"description": "Tracks my gym sets and my skiing", "settings": [{"key": "K"}],
           "mcp": [{"name": "g", "url": "https://g", "headers": {"A": "${K}"}}]}
    assert "CREDENTIAL_IN_AGENT_TOML" not in _codes(raw)


# ── settings ────────────────────────────────────────────────────────────────
def test_a_field_nothing_reads_is_flagged():
    """The user fills it in and nothing happens — usually a typo against the name that IS read."""
    raw = {
        "settings": [{"key": "COINBASE_API_KEY"}],
        "mcp": [{"name": "cb", "url": "https://cb", "headers": {"A": "Bearer ${COINBASE_KEY}"}}],
    }
    codes = _codes(raw)
    assert "SETTING_NEVER_USED" in codes  # the declared one
    assert "MCP_UNDECLARED_SETTING" in codes  # the typo'd one


def test_a_field_read_by_the_app_counts_as_used():
    raw = {"settings": [{"key": "DASH_URL"}]}
    assert "SETTING_NEVER_USED" not in _codes(raw, {"ui/board.js": "fetch('${DASH_URL}')"})


def test_a_third_party_api_key_name_is_perfectly_fine():
    """`COINBASE_API_KEY` is what the build-agent skill tells authors to write. The first
    version of this rule flagged anything ending in `_API_KEY` — a check that fires on its own
    documented example."""
    raw = {"settings": [{"key": "COINBASE_API_KEY"}], "mcp": [
        {"name": "cb", "url": "https://cb", "headers": {"A": "${COINBASE_API_KEY}"}}]}
    assert "SETTING_SHADOWS_SHARED_KEY" not in _codes(raw)


def test_declaring_a_provider_key_as_your_own_is_flagged():
    """Provider keys are one machine-wide credential shared by every agent. An agent's settings
    page offering to overwrite ANTHROPIC_API_KEY is not offering its own field."""
    raw = {"settings": [{"key": "ANTHROPIC_API_KEY"}], "mcp": [
        {"name": "x", "url": "https://x", "headers": {"A": "${ANTHROPIC_API_KEY}"}}]}
    assert "SETTING_SHADOWS_SHARED_KEY" in _codes(raw)


def test_declaring_an_agentd_name_is_flagged():
    raw = {"settings": [{"key": "AGENTD_STATE_DIR"}], "mcp": [
        {"name": "x", "url": "https://x", "headers": {"A": "${AGENTD_STATE_DIR}"}}]}
    assert "SETTING_SHADOWS_SHARED_KEY" in _codes(raw)


# ── MCP ─────────────────────────────────────────────────────────────────────
def test_a_server_referencing_an_undeclared_setting_is_an_error():
    """The daemon refuses to connect a server whose credential is empty, so this agent would
    silently have no tools — the failure mode the whole feature exists to prevent."""
    raw = {"mcp": [{"name": "aws", "command": ["uvx", "x"], "env": {"K": "${AWS_KEY}"}}]}
    assert "MCP_UNDECLARED_SETTING" in _codes(raw)
    assert _level(raw, "MCP_UNDECLARED_SETTING") == "error"


def test_a_server_with_neither_transport_is_an_error():
    raw = {"mcp": [{"name": "aws"}]}
    assert "MCP_TRANSPORT" in _codes(raw)


def test_a_server_with_both_transports_is_an_error():
    raw = {"mcp": [{"name": "aws", "command": ["x"], "url": "https://y"}]}
    assert "MCP_TRANSPORT" in _codes(raw)


def test_allowing_a_namespace_no_server_declares_is_flagged():
    """THE headline case, from the other side: the author allowed `aws__*`, no [[mcp]] provides
    it, and on another machine those tools simply do not exist. Every symptom is the model
    saying it cannot do the thing."""
    raw = {"tools": {"allow": ["read", "aws__describe_instances"]}, "settings": [{"key": "K"}],
           "mcp": [{"name": "other", "url": "https://o", "headers": {"A": "${K}"}}]}
    assert "ALLOW_UNKNOWN_MCP_NAMESPACE" in _codes(raw)


def test_allowing_a_namespace_that_is_declared_is_fine():
    raw = {"tools": {"allow": ["aws__describe"]}, "settings": [{"key": "K"}],
           "mcp": [{"name": "aws", "url": "https://a", "headers": {"A": "${K}"}}]}
    assert "ALLOW_UNKNOWN_MCP_NAMESPACE" not in _codes(raw)


def test_a_plain_tool_name_is_not_mistaken_for_a_namespace():
    raw = {"tools": {"allow": ["read", "write", "exec"]}, "settings": [{"key": "K"}],
           "mcp": [{"name": "a", "url": "https://a", "headers": {"H": "${K}"}}]}
    assert "ALLOW_UNKNOWN_MCP_NAMESPACE" not in _codes(raw)


def test_an_oauth_reference_to_no_such_connection_is_an_error():
    raw = {"mcp": [{"name": "h", "url": "https://h", "auth": "oauth:myhealth"}]}
    assert "MCP_UNDECLARED_OAUTH" in _codes(raw)


def test_an_oauth_reference_that_resolves_is_fine():
    raw = {
        "mcp": [{"name": "h", "url": "https://h", "auth": "oauth:myhealth"}],
        "oauth": [{"name": "myhealth", "server": "https://h"}],
    }
    assert "MCP_UNDECLARED_OAUTH" not in _codes(raw)


# ── OAuth ───────────────────────────────────────────────────────────────────
def test_a_connection_with_nowhere_to_send_the_user_is_an_error():
    raw = {"oauth": [{"name": "x"}]}
    assert "OAUTH_NO_ENDPOINTS" in _codes(raw)


def test_explicit_endpoints_without_a_client_id_are_flagged():
    raw = {"oauth": [{"name": "x", "authorize_url": "https://a", "token_url": "https://t"}]}
    assert "OAUTH_NO_CLIENT_ID" in _codes(raw)


def test_a_discovered_server_needs_no_client_id():
    """A server supporting dynamic registration issues one — demanding it up front would flag
    the zero-config case that is the whole point of discovery."""
    assert "OAUTH_NO_CLIENT_ID" not in _codes({"oauth": [{"name": "x", "server": "https://s"}]})


def test_an_oauth_block_referencing_an_undeclared_setting_is_an_error():
    raw = {"oauth": [{"name": "x", "server": "https://s", "client_id": "${X_CLIENT_ID}"}]}
    assert "OAUTH_UNDECLARED_SETTING" in _codes(raw)


# ── shipping ────────────────────────────────────────────────────────────────
def test_any_declaration_says_the_agent_is_local_only():
    """Said BEFORE publishing rather than discovered after: the values live in one machine's
    .env, a stdio server spawns a local process, the callback is a loopback URL."""
    for raw in ({"settings": [{"key": "K"}]}, {"mcp": [{"name": "m", "url": "https://m"}]},
                {"oauth": [{"name": "o", "server": "https://o"}]}):
        assert "DECLARATIONS_ARE_LOCAL_ONLY" in _codes(raw)


def test_an_agent_with_servers_and_no_workshop_answer_is_told_it_inherits():
    raw = {"settings": [{"key": "K"}], "mcp": [{"name": "m", "url": "https://m", "headers": {"A": "${K}"}}]}
    assert "MCP_WORKSHOP_INHERITED" in _codes(raw)


def test_saying_so_explicitly_silences_it():
    assert "MCP_WORKSHOP_INHERITED" not in _codes(WIRED)

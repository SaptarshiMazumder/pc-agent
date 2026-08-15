"""RULEBOOK — the ONE policy table for every agent-authoring rule.

DETECTION lives with the knowledge (a TOML shape check cannot share a function with a
Python-source scan): agent_layout_rules, packageability_rules, sandbox_rules,
tool_grant_rules, portability_rules, ui_rules each know how to SPOT a violation.
POLICY — how bad it is, and which gates it closes — lives here, once. Tightening or
loosening a screw is editing one row; adding a rule is one check + one row. Before this
table, the pack gate carried its own frozenset (`_SHIPS_BROKEN`), publish would have grown
another, and severity lived wherever each check hardcoded it — three places to keep
agreeing about one decision.

Two knobs per rule:

  level          severity OVERRIDE. ``None`` keeps whatever the check emitted, so a code
                 absent from this table (or a rule module used standalone in a test) is
                 never silently repriced. Only `error` findings fail validation outright.
  blocks         gates this code closes EVEN when its level is warn/info: ``PACK`` (a
                 .agentpkg is the moment work stops being local — side-loaded installs
                 included), ``PUBLISH`` (a public marketplace listing). Errors always block
                 both via the ok-gate; `blocks` exists for findings that are advisory on the
                 author's machine and unacceptable in an artifact.

DELIBERATELY CODE, NOT CONFIG. This table gates what authors may ship; policy an author
could edit next to their agent would not be policy. It is still data — one dict, versioned
with the rules it governs, and the tests pin every row that guards something.

The rules the table does NOT list are the RUNTIME refusals, enforced at choke points the
model cannot route around (each keyed off a single domain constant): the tenant fence
(write_scope.check_read/check_write), exec refusing fenced runs (shell plugin), reserved /
malformed ids (domain.agent.invalid_new_agent_id), the ownership stamp failing a create
(file_registry.create_from), and installed agents' write_roots clamped to their own folder
(agent_service._installed_write_clamp). They appear here as prose so this file is the whole
catalogue, but their enforcement needs no table: it cannot be skipped.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .finding import Finding

PACK = "pack"
PUBLISH = "publish"


@dataclass(frozen=True)
class Rule:
    level: str | None = None  # severity override; None = as the check emitted it
    blocks: tuple[str, ...] = ()  # gates closed even when level is warn/info
    note: str = ""  # why the rule exists — one line, for humans reading the table


RULEBOOK: dict[str, Rule] = {
    # ---- layout (agent_layout_rules) ------------------------------------------------
    "NO_IDENTITY": Rule(note="IDENTITY.md is the agent's bootstrap; error"),
    "APP_TABLE_STRAY_DISPLAY_KEY": Rule(note="display keys inside [app] are silently dropped"),
    "APP_TABLE_UNKNOWN_KEY": Rule(note="unknown [app] key does nothing"),
    "APP_BAD_MODE": Rule(note="[app] mode must be window|browser"),
    "SKILL_NO_MD": Rule(note="a skill dir without SKILL.md loads nothing; error"),
    "PLUGIN_NO_MANIFEST": Rule(note="a private plugin without plugin.toml is invisible; error"),
    "PLUGIN_NO_MODULE": Rule(note="a private plugin needs at least one .py; error"),
    # ---- packageability (packageability_rules) --------------------------------------
    "ORPHANED_UI": Rule(note="ui/ with no [app] — the window can never open; error"),
    "NOT_A_PRODUCT": Rule(note="chat-only agent; info"),
    "APP_ENTRY_MISSING": Rule(note="[app] entry file absent — 404 at launch; error"),
    "UI_NO_SDK": Rule(note="no vendored agentd-client.js — page cannot talk to the daemon"),
    "UI_NOT_BUILT": Rule(
        note="app/ sources with no ui/ output — app/ never ships, so the agent installs with "
        "no window at all; error"
    ),
    "NO_VERSION": Rule(
        blocks=(PUBLISH,),
        note="installs supersede BY VERSION; a version-less publish can never be updated",
    ),
    "DEFINITION_IN_EXCLUDED_DIR": Rule(
        note="definition files under an excluded dir ship to nobody"
    ),
    "WORKSPACE_NOT_SHIPPED": Rule(
        note="workspace/ never ships, and on hosted every user starts with an EMPTY one"
    ),
    # ---- sandbox (sandbox_rules) — advisory while authoring, certain breakage or a ----
    # ---- security hole once the agent is installed on a machine that is not yours ----
    "PRIVATE_TOOLS_UNTRUSTED": Rule(note="reminder of the tier; info"),
    "UNTRUSTED_WANTS_SECRETS": Rule(
        blocks=(PACK, PUBLISH), note="granted secrets = {} — reads nothing, silently"
    ),
    "UNTRUSTED_WANTS_NETWORK": Rule(
        blocks=(PACK, PUBLISH), note="imports an HTTP client; a sandboxed tool has no socket"
    ),
    "UNTRUSTED_MAYBE_NETWORK": Rule(note="heuristic (a URL somewhere) — advisory only"),
    "UNTRUSTED_WANTS_SPAWN": Rule(
        blocks=(PACK, PUBLISH), note="subprocess/os.system — denied outright in the sandbox"
    ),
    "UNTRUSTED_MODEL_UNDECLARED": Rule(
        blocks=(PACK, PUBLISH), note="no needs_model => the broker refuses every call"
    ),
    # ---- grants (tool_grant_rules) ---------------------------------------------------
    "COMPANION_TOOL_MISSING": Rule(note="half a tool pair (exec without process)"),
    "COMPANION_TOOL_DENIED": Rule(note="the pair was denied on purpose — say so to the agent"),
    # ---- portability (portability_rules) — machines that are not the author's --------
    "WIDE_WRITE_ROOTS": Rule(
        blocks=(PACK, PUBLISH),
        note="write scope beyond <agent_dir> is builder-grade reach; the runtime clamps "
        "installed copies, and nothing that needs clamping belongs in an artifact",
    ),
    "EXEC_ON_WEB": Rule(
        blocks=(PUBLISH,),
        note="exec is refused on every hosted run — a web-delivered agent depending on it "
        "ships broken for exactly the users [delivery] web = true is for",
    ),
    "WEB_REQUIRES_LOCAL": Rule(
        blocks=(PUBLISH,),
        note="requires_local agents are withheld from hosted daemons entirely — web = true "
        "promises a delivery that cannot happen",
    ),
    "HEARTBEAT_WITHOUT_AUTONOMY": Rule(
        note="heartbeat never fires unless [capabilities] autonomy = true"
    ),
    # ---- ui (ui_rules) ---------------------------------------------------------------
    "EVENT_PAYLOAD_NOT_NESTED": Rule(note="payload.event.type, not payload.type; error"),
    "UNKNOWN_EVENT": Rule(note="listening for an event nobody emits; error"),
    "METHOD_NOT_APP_CALLABLE": Rule(note="calling a HOST-tier method from an app; error"),
    "UNKNOWN_SDK_METHOD": Rule(note="calls a client.* the vendored SDK does not define; warn"),
    # ---- declarations (declaration_rules) — [[settings]] / [[mcp]] / [[oauth]] --------
    # coherence, all INVISIBLE until someone else installs the agent. The ERROR rows block
    # both gates already via the ok-gate; listed here so the catalogue is complete and a
    # future screw-turn is one row.
    "CREDENTIAL_IN_AGENT_TOML": Rule(
        blocks=(PACK, PUBLISH),
        note="a real secret inlined in agent.toml ships to every buyer — error AND an "
        "explicit gate block, so even a downgraded severity can never leak a key in an artifact",
    ),
    "SETTING_SHADOWS_SHARED_KEY": Rule(note="a [[settings]] field masks a daemon-shared key"),
    "SETTING_NEVER_USED": Rule(note="declared setting no [[mcp]]/[[oauth]] block reads"),
    "MCP_NO_NAME": Rule(note="an [[mcp]] block without a name cannot be wired; error"),
    "MCP_TRANSPORT": Rule(note="[[mcp]] transport must be stdio|url; error"),
    "MCP_UNDECLARED_SETTING": Rule(note="[[mcp]] references a setting nothing declares; error"),
    "MCP_UNDECLARED_OAUTH": Rule(note="[[mcp]] references an [[oauth]] nothing declares; error"),
    "ALLOW_UNKNOWN_MCP_NAMESPACE": Rule(
        note="[tools] allow names an mcp namespace no [[mcp]] provides — advisory (an operator "
        "server on the machine could provide it), so it warns, never gates"
    ),
    "OAUTH_NO_ENDPOINTS": Rule(note="[[oauth]] without auth/token endpoints cannot sign in; error"),
    "OAUTH_NO_CLIENT_ID": Rule(note="[[oauth]] with no client id — advisory"),
    "OAUTH_UNDECLARED_SETTING": Rule(note="[[oauth]] references a setting nothing declares; error"),
    "DECLARATIONS_ARE_LOCAL_ONLY": Rule(note="reminder these blocks resolve per-machine; info"),
    "MCP_WORKSHOP_INHERITED": Rule(note="mcp_workshop inherited from the daemon default; info"),
}


def apply_policy(findings: tuple[Finding, ...]) -> tuple[Finding, ...]:
    """Reprice findings per the table. The seam every screw-turn goes through: a check keeps
    emitting what it knows, the table decides what it weighs."""
    out = []
    for f in findings:
        rule = RULEBOOK.get(f.code)
        if rule is not None and rule.level and rule.level != f.level:
            f = replace(f, level=rule.level)
        out.append(f)
    return tuple(out)


def blockers(gate: str) -> frozenset[str]:
    """Codes that close ``gate`` even at warn/info level (errors block via the ok-gate)."""
    return frozenset(code for code, rule in RULEBOOK.items() if gate in rule.blocks)

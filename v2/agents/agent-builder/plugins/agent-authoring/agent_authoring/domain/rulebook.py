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

from .finding import ERROR, Finding

PACK = "pack"
PUBLISH = "publish"

#: Distributing to an ORGANIZATION — an enterprise handing an agent to its own people.
#:
#: Deliberately NOT a third column in the table. An org share is a side-loaded install to everyone
#: in the company, which is precisely what PACK already describes ("the moment work stops being
#: local — side-loaded installs included"). Giving it its own rows would mean curating two lists
#: that have to agree forever, and the day they drift is the day an org ships something the
#: marketplace would have refused. So it RESOLVES to the pack set: ONE bar for "this left my
#: machine and reached other people", whoever those people are.
#:
#: What it does NOT carry over is the marketplace's REVIEW. Approval is a platform judgement about
#: a public listing; these are quality checks about a working agent. An enterprise gets instant
#: distribution — no queue, no operator — and still cannot hand two hundred colleagues a half-built
#: shell.
ORG_SHARE = "org_share"


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
    # EVERY AGENT WITH A WINDOW SIGNS ITS USER IN. Not a recommendation — an agent that ships
    # without it has no way to know who is using it, and on a hosted install every model call
    # fails with a provider error and nothing on screen explains why. The check was a warning
    # for exactly as long as it took someone to publish past it.
    "UI_NO_SIGN_IN": Rule(
        level=ERROR,
        blocks=(PACK, PUBLISH),
        note="an app with no sign-in cannot be packed or published; the SDK's gate is the only "
        "mechanism — never a second login of the agent's own",
    ),
    # AND EVERY AGENT WITH A WINDOW SELLS CREDITS. The same argument, one step later in the same
    # story: running out of credits is the ONE failure a user can fix themselves, and an agent
    # that cannot take the top-up simply stops working and says nothing. The user has to already
    # know a separate app exists, find it, and buy there. Nobody does. They uninstall.
    #
    # It blocks PACK and PUBLISH rather than merely warning for the reason the sign-in rule
    # learned the hard way: a warning is a thing you publish past.
    "UI_NO_CREDITS": Rule(
        level=ERROR,
        blocks=(PACK, PUBLISH),
        note="an app with no credits panel cannot be packed or published; the copied "
        "common/credits module is the only mechanism — never a store of the agent's own",
    ),
    # AND EVERY AGENT WITH A WINDOW CAN BE CONFIGURED FROM INSIDE IT. Third in the same story: an
    # agent whose model, turn limit or keys can only be changed from another application is an
    # agent whose owner goes looking in the assistant's settings window for a page that knows
    # nothing about it. The module is copied in by the scaffold — this catches the agent that
    # shipped the files and never rendered them, which is the same trap the credits rule names.
    "UI_NO_SETTINGS": Rule(
        level=ERROR,
        blocks=(PACK, PUBLISH),
        note="an app with no settings page cannot be packed or published; common/settings is the "
        "only mechanism — the same page the assistant shows, plus this agent's own layer",
    ),
    # THE SHARED MODULES MUST STILL BE THE SHARED MODULES. `app/src/common/` is copied into every
    # agent — accounts and money — and a copy is editable. The edits are always reasonable at the
    # time; what ends up different is credential and payment handling, in an artifact that is then
    # published. MODIFIED is the dangerous one of the two: it still builds, so nothing else looks
    # wrong. Both close PACK and PUBLISH, because both are only a problem once it ships.
    "UI_COMMON_MISSING": Rule(
        level=ERROR,
        blocks=(PACK, PUBLISH),
        note="a shared module is absent — re-copy it from templates/_common/, never rewrite it",
    ),
    "UI_COMMON_MODIFIED": Rule(
        level=ERROR,
        blocks=(PACK, PUBLISH),
        note="a shared module was edited — restore it; change the SOURCE if every agent needs it",
    ),
    # THE OTHER HALF OF THE SAME BARGAIN. The shared modules carry no colours and no fonts so that
    # each agent can look like itself; an agent that does not hold up its end ships pages that
    # render transparent. It BLOCKS for the same reason MODIFIED does — it still builds, so
    # nothing downstream would ever catch it.
    # THE FOURTH MANDATORY PIECE. Blocks for the same reason the other three do: an agent that
    # cannot be shared with a colleague is not an agent a company can buy, and the failure is
    # silent — the window works perfectly for whoever installed it.
    # SCAFFOLDING MUST NOT SHIP. Advisory while the agent is being built — the template's widgets
    # are supposed to be there on day one, and an error every time you validate would train you to
    # ignore validation. It closes the artifact gates instead: the moment work leaves this machine,
    # a window still made of the template's examples is an unfinished agent with a finished look.
    "UI_PLACEHOLDER_SHIPPED": Rule(
        blocks=(PACK, PUBLISH),
        note="template scaffolding still tagged @placeholder — adopt it or delete it before shipping",
    ),
    "UI_NO_ORGS": Rule(
        level=ERROR,
        blocks=(PACK, PUBLISH),
        note="an app with no organizations page cannot be packed or published; the copied "
        "common/orgs module is the only mechanism — never a team page of the agent's own",
    ),
    "UI_TOKENS_MISSING": Rule(
        level=ERROR,
        blocks=(PACK, PUBLISH),
        note="the shared modules read CSS custom properties this app never defines — its "
        "settings, credits and sign-in pages render with no background and no accent",
    ),
    # ONE SIGN-IN IMPLEMENTATION ON THIS PLATFORM. Listed here rather than left to block on its
    # error level alone: this table is where the shipping policy is written down, and a code that
    # blocks only because of how it happens to be priced is a code somebody later re-prices to a
    # warning without ever reading that it was load-bearing.
    #
    # There were three copies of sign-in once. They drifted — one would not renew a token that had
    # already expired, one had no single-flight guard and got whole refresh-token families revoked,
    # and they posted to different endpoints. Users were signed out ten minutes in, and signing
    # back in did not help. An agent that writes a fourth copy inflicts that on its own users only.
    "UI_OWN_LOGIN": Rule(
        level=ERROR,
        blocks=(PACK, PUBLISH),
        note="an agent that mints or stores credentials itself cannot be packed or published — "
        "<Gate> from common/auth draws the form, identity().accessToken() hands over a "
        "credential",
    ),
    # A WINDOW BUILT FROM SOURCE THAT NO LONGER EXISTS. `app/` is compiled into `ui/`, and only
    # `ui/` is served, packed and published — so an agent whose source has moved on from its build
    # looks finished from every angle its author can see and hands everyone else the older screen.
    #
    # Listed here rather than left to its error level, for the same reason as UI_OWN_LOGIN: this
    # table is where shipping policy is written down. It is also the newest way to get this wrong —
    # building used to be part of editing, and it is now a separate step somebody has to remember.
    "APP_BUILD_STALE": Rule(
        level=ERROR,
        blocks=(PACK, PUBLISH),
        note="ui/ predates app/src — the packer ships ui/, so this would deliver the old window; "
        "call build_app",
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
    "AUTHORED_SETTING_VALUE": Rule(
        blocks=(PACK, PUBLISH),
        note="the AUTHOR filled in a value for a field the OWNER supplies. Error AND an explicit "
        "gate block, for the same reason as the row above: agent.config.json ships, so the "
        "author's answer becomes every installer's default. It is also how an agent ends up "
        "running on credentials nobody chose — an empty referenced setting is what makes the "
        "daemon ASK, and a filled-in value silently removes the question",
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


#: POLISH, not safety. These close the PUBLIC gates and deliberately do NOT close an org share.
#:
#: WHY THE DESTINATIONS DIVERGE HERE. A marketplace listing reaches strangers who did not ask for
#: it and cannot see inside it, so "this is still the starter template" is the platform's business.
#: An org share is a company handing its own staff an internal tool: the audience is named, it can
#: ask the author directly, and how finished an internal screen needs to be is that company's
#: judgement. Refusing there was us holding an opinion about someone else's work in their own
#: building.
#:
#: IT ALSO FIRED ON EVERYTHING. The scaffold ships these widgets AND renders them from App.tsx, so
#: every agent failed this check from birth until somebody did the chore by hand. A gate that 100%
#: of artifacts fail is not a bar, it is a toll, and it teaches authors that the validator is
#: something to get past rather than something to read.
#:
#: SAFETY IS NOT IN HERE, and must never be: a credential in agent.toml, a sandboxed tool reaching
#: for secrets or the network, builder-grade write scope. Those close BOTH destinations, because
#: they hurt whoever installs the agent, whoever that turns out to be.
POLISH_ONLY = frozenset({"UI_PLACEHOLDER_SHIPPED"})


def blockers(gate: str) -> frozenset[str]:
    """Codes that close ``gate`` even at warn/info level (errors block via the ok-gate)."""
    if gate == ORG_SHARE:
        # The PACK bar MINUS the polish-only codes: everything that protects the person who
        # installs it, nothing that is only an opinion about how finished it looks.
        return frozenset(c for c, r in RULEBOOK.items() if PACK in r.blocks) - POLISH_ONLY
    return frozenset(code for code, rule in RULEBOOK.items() if gate in rule.blocks)

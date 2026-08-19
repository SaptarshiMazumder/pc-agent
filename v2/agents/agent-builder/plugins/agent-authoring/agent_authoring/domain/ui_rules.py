"""UiRules — does the agent's own app code stand a chance of working?

Every other rule in this bundle checks STRUCTURE: does the entry file exist, is the TOML
ordered, will it package. None of them opens `ui/app.js`. So an app whose every event branch
is dead passes validation and ships, and the failure is invisible: the socket connects, the
console logs, and the screen never changes.

That is not hypothetical. A generated agent shipped with:

    client.onAgent('inbox-triage', (payload) => {
      if (payload.type === 'tool_execution_start')   // never true — it is payload.event.type
      else if (payload.type === 'message_delta')     // never true — no such event

Both mistakes are visible in the file. Nothing looked.

PURE by construction: the source text comes in, the known-good names come in, findings come
out. The names are INJECTED rather than copied — `APP_FACING_EVENTS` lives in the runtime's
domain and `APP_SCOPED_METHODS` in its gateway, and a second copy here would be one more thing
to drift out of date, which is the exact failure this rule exists to catch.

Deliberately conservative. A regex is not a parser, so every check below is anchored on a
distinctive call shape and stays silent when unsure — a false alarm on working code trains the
model to ignore the report, which costs more than a missed defect.
"""

from __future__ import annotations

import re

from .finding import ERROR, WARN, Finding
from .js_comment_stripper import JsCommentStripper

_STRIPPER = JsCommentStripper()

# ── UI components ──────────────────────────────────────────────────────────────────────
# What a component IS, how to tell it is present, and which SDK symbols it needs are all declared
# ONCE, in `ui_component.py`, and this rule is GIVEN that catalogue. It used to keep its own
# regexes for sign-in, which meant the snippet existed in three unshared places — the chat-app
# template, here, and the tool that inserts it. Three copies of one fact is exactly the drift these
# rules exist to catch, and it is the same argument the module docstring already makes for event
# names: told the real vocabulary rather than keeping a copy.
#
# Two findings come out of it, and they fail differently:
#
#   * sign-in missing -> WARN. The agent works on the author's own keys; it is unusable only on a
#     HOSTED install. The failure there is silent and total: the window opens, the composer works,
#     the daemon has no platform credential, and every send returns a provider error that reads as
#     "the model is down" rather than "this app never asks anyone to log in".
#   * a component present whose SDK symbol is missing -> ERROR. A guaranteed "not a function" on
#     load: a dead window, every time. ONE rule for N components, driven by `requires`.
_SDK_VENDOR_SUFFIX = "vendor/agentd-client.js"


def is_built_app(files) -> bool:
    """Is this app COMPILED from source rather than hand-written into ``ui/``?

    The convention, and the only signal worth keying on: source in ``app/`` with its own
    ``package.json``, bundler output in ``ui/``. Two rules depend on knowing the difference —
    ``ui/`` is unreadable machine output for such an app, and the SDK is inside the bundle rather
    than beside it as a vendored file.
    """
    return any(f == "app/package.json" for f in files)


def app_sources(sources: dict) -> dict:
    """The agent's OWN app code, whichever layout it uses.

    Three exclusions, each for a different reason:

      ui/vendor/**   the SDK, vendored verbatim — not the agent's code
      ui/assets/**   BUILD OUTPUT. Minified, identifiers renamed. Running name-matching rules over
                     it yields either vacuous passes or nonsense hits on mangled variables.
      anything else  not code these rules understand

    When ``app/src/`` is present it is the real source, and it is read INSTEAD of ``ui/`` — a
    built app has nothing readable there.
    """
    src = {
        rel: text
        for rel, text in sources.items()
        if rel.startswith("app/src/") and rel.endswith((".ts", ".tsx", ".js", ".jsx"))
    }
    if src:
        return src
    return {
        rel: text
        for rel, text in sources.items()
        if rel.startswith("ui/")
        and rel.endswith(".js")
        and "/vendor/" not in rel
        and not rel.startswith("ui/assets/")
    }
# The component whose absence is worth warning about. Others are opt-in features; this one is the
# difference between an agent that can be sold and one that cannot.
_REQUIRED_COMPONENT = "sign-in"

#: Reaching the accounts service, or a credential store, WITHOUT the SDK in between.
#:
#: Each of these is something only the shared implementation should ever do. The endpoints mint and
#: rotate credentials; the storage keys are where the one implementation keeps them, and a second
#: writer there is two things fighting over one slot.
_OWN_LOGIN_PATTERNS = (
    r"/auth/(?:login|refresh|logout|logout-all)\b",
    r"[\"'`]/login[\"'`]",
    r"\brefresh_token\b",
    r"\bagentd\.(?:session|refresh|auth)\b",
)

# `payload.type` / `p.type` etc. — reading the wrapper as if it were the event.
_PAYLOAD_DOT_TYPE = re.compile(r"\b(\w+)\.type\b")
# The handler shapes that hand you the WRAPPER, so we know which identifier is the payload.
# Both spellings matter: an inline arrow, and a NAMED function passed by reference — the two
# hand-written agents in this repo use the named form, and only matching arrows made the rule
# silently pass over a genuine dead branch in both.
_HANDLER = re.compile(r"\.(?:onRun|onAgent)\s*\([^)]*?\(\s*(\w+)\s*\)\s*=>")
_ON_CHAT_EVENT = re.compile(r"\.on\s*\(\s*['\"]chat\.event['\"]\s*,\s*\(\s*(\w+)\s*\)\s*=>")
# `.onRun(session, onEvent)` / `.on('chat.event', onEvent)` -> the callback's NAME
_HANDLER_REF = re.compile(
    r"\.(?:onRun|onAgent)\s*\([^,()]+,\s*(\w+)\s*\)"
    r"|\.on\s*\(\s*['\"]chat\.event['\"]\s*,\s*(\w+)\s*\)"
)
# `function onEvent(payload)` -> its first parameter
_FUNC_PARAM = "(?:function\\s+{name}\\s*\\(\\s*(\\w+)|(?:const|let|var)\\s+{name}\\s*=\\s*\\(?\\s*(\\w+)\\s*\\)?\\s*=>)"

# `const ev = payload.event` — the variable that actually holds an event
_EVENT_ALIAS = re.compile(r"(?:const|let|var)\s+(\w+)\s*=\s*(\w+)\.event\b")
# `ev.type === 'x'` / `ev.kind === "x"` — a comparison against a KNOWN event variable
_TYPED_COMPARE = re.compile(r"\b{var}\.(?:type|kind)\s*===?\s*['\"]([\w.]+)['\"]")
# `switch (ev.type) { ... }` — the case labels inside belong to that variable
_SWITCH = re.compile(r"switch\s*\(\s*{var}\.(?:type|kind)\s*\)\s*\{{")
_CASE = re.compile(r"case\s+['\"]([\w.]+)['\"]")
# client.request('some.method') — a raw RPC call
_RPC_CALL = re.compile(r"\.request\s*\(\s*['\"]([a-z][\w.]+)['\"]")
# client.<method>( — an SDK call
_SDK_CALL = re.compile(r"\bclient\.([a-zA-Z]\w*)\s*\(")


class UiRules:
    """Reads `ui/*.js` and reports what cannot work."""

    name = "ui"

    def __init__(self, events: frozenset[str], kinds: frozenset[str], methods: frozenset[str],
                 sdk_methods: frozenset[str], components=()):
        """:param components: the UiComponent catalogue. Injected for the same reason `events` is —
        so "what a component looks like in code" has exactly one definition, in the module that
        also inserts it. Defaults to empty so a caller that does not care about components (a test
        of the event rules, say) needs no extra argument."""
        self._events = events
        self._kinds = kinds
        self._methods = methods
        self._sdk = sdk_methods
        self._components = tuple(components)

    def check(self, spec, raw_toml: dict, files: list[str], sources: dict) -> list[Finding]:
        """`sources` maps an agent-relative path -> its text. Only the agent's OWN app code is
        considered — see `app_sources`."""
        out: list[Finding] = []
        for rel, src in sorted(app_sources(sources).items()):
            # Comments are not code. Without this the rules fire on a file that WARNS about
            # the very mistake they check for — which is the most careful code they will ever
            # see, and exactly the false alarm that gets a check switched off.
            code = _STRIPPER.strip(src)
            out += self._nested_payload(rel, code)
            out += self._unknown_events(rel, code)
            out += self._unknown_calls(rel, code)
        out += self._sign_in(raw_toml, sources)
        return out

    # ---------------------------------------------------------------- UI components
    def _sign_in(self, raw_toml: dict, sources: dict) -> list[Finding]:
        """Whole-agent check (not per-file): which components does this app use, and can the SDK it
        ships actually run them?

        Deliberately quiet otherwise. An agent with no `[app]` has no page to sign in on, and one
        with no ui/*.js has not been scaffolded yet — neither is a mistake, and warning about either
        is how a check earns its way into being ignored.
        """
        if not isinstance(raw_toml.get("app"), dict):
            return []
        own = app_sources(sources)
        if not own:
            return []

        code = "\n".join(_STRIPPER.strip(src) for src in own.values())
        installed = [c for c in self._components if self._present(c, code)]

        # BEFORE the missing-gate check, because an app that reached for the accounts service
        # itself plainly HAS a sign-in — telling it there is none would send the author looking
        # for the wrong thing. The problem is whose implementation, not whether one exists.
        rolled = self._own_login(code)
        if rolled:
            return rolled

        if not any(c.id == _REQUIRED_COMPONENT for c in installed):
            # MANDATORY, not advisory. Every agent with a window signs its user in: the window is
            # the only place that knows whether anyone is there, and an agent that never asks has
            # no identity to attribute anything to. On a hosted install it is also fatal —
            # every model call fails with a provider error and nothing on screen says why.
            #
            # ONE MECHANISM. The SDK's gate, on the SDK's endpoints. An agent that grows its own
            # login is a second front door onto the same accounts service, written once by
            # somebody who was not thinking about credentials that day.
            return [
                Finding(
                    ERROR,
                    "UI_NO_SIGN_IN",
                    "this app never signs the user in. Every agent with a window must — it is "
                    "how the agent knows who is using it, and on a hosted install every model "
                    "call fails without it, with nothing on screen to explain why.",
                    path="ui/app.js",
                    fix=f"add_ui_component('{{agent}}', '{_REQUIRED_COMPONENT}') — it does every "
                    "step (SDK, script tag, theme tokens, the call itself) and is safe to re-run. "
                    "In a React app, call `mountSignInGate()` from @agentd/client BEFORE the "
                    "first render (see main.tsx in the React starter). Do NOT write your own "
                    "login form: the gate's element ids are what the packaged-build login test "
                    "drives, and a second implementation is a second way to get credentials "
                    "wrong. The gate renders NOTHING when a stored session still works.",
                )
            ]

        # ONE rule for every component, driven by its declared `requires`. Adding component #2
        # needs no new rule here — which is the point of the catalogue being injected.
        vendored = next(
            (src for rel, src in sources.items() if rel.endswith(_SDK_VENDOR_SUFFIX)), ""
        )
        if not vendored:
            return []
        for component in installed:
            missing = [symbol for symbol in component.requires if symbol not in vendored]
            if missing:
                return [
                    Finding(
                        ERROR,
                        "UI_SDK_PREDATES_COMPONENT",
                        f"this app uses the '{component.id}' component, but the vendored SDK in "
                        f"ui/vendor/agentd-client.js has no {', '.join(missing)} — the app will die "
                        f"on load with 'agentd.{missing[0]} is not a function'.",
                        path=_SDK_VENDOR_SUFFIX,
                        fix="re-vendor the SDK: `npm run build` in v2/clients/sdk-js (its vendor "
                        "step refreshes every agent's copy).",
                    )
                ]
        return []

    @staticmethod
    def _own_login(code: str) -> list[Finding]:
        """An app that talks to the accounts service ITSELF, rather than through the SDK.

        WHY THIS IS AN ERROR AND NOT A STYLE NOTE. There were three implementations of sign-in in
        this codebase — the agentd client's, the SDK's, and a push-down path — and they drifted
        exactly as three copies of one job do. The SDK's copy would not renew a token that had
        already expired, had no single-flight guard (so two windows waking together tripped the
        server's refresh-reuse detector and revoked the whole family), and posted to a different
        endpoint than the other one. Users were signed out ten minutes after signing in, and
        signing back in did not help. All three are now one implementation, and an agent that
        reaches past it re-creates the problem for its own users only — the hardest kind to find.

        WHAT AN AGENT SHOULD DO INSTEAD is never write a credential path at all: `mountSignInGate()`
        for the form, and `identity().accessToken()` when it needs a credential, which renews first
        if what it holds is spent.

        Matched on the ENDPOINT and the storage key, not on the word "login" — an agent is welcome
        to have a page, a button and a route by that name. What it may not do is mint or keep a
        credential of its own.
        """
        reached = sorted(
            {
                found.group(0)
                for pattern in _OWN_LOGIN_PATTERNS
                for found in re.finditer(pattern, code)
            }
        )
        if not reached:
            return []
        return [
            Finding(
                ERROR,
                "UI_OWN_LOGIN",
                "this app handles credentials itself ("
                + ", ".join(reached[:4])
                + ") instead of going through the SDK. Every client on this platform shares one "
                "sign-in implementation; a second one is a second set of renewal bugs, and the "
                "last set signed users out ten minutes after they signed in.",
                path="ui/app.js",
                fix="delete it and use the SDK: `await agentd.mountSignInGate()` draws the form, "
                "and `agentd.identity().accessToken()` hands you a credential — renewing first "
                "when the one it holds has expired. Neither needs an endpoint or a storage key "
                "from you.",
            )
        ]

    @staticmethod
    def _present(component, code: str) -> bool:
        return any(re.search(insertion.detect, code) for insertion in component.insert)

    # ---------------------------------------------------------------- payload shape
    def _nested_payload(self, rel: str, src: str) -> list[Finding]:
        """`onRun(p => ...)` hands you `{sessionKey, runId, agentId, ts, event}`. Switching on
        `p.type` misses every branch — the type is one level down, in `p.event.type`."""
        handlers = {m.group(1) for m in _HANDLER.finditer(src)}
        handlers |= {m.group(1) for m in _ON_CHAT_EVENT.finditer(src)}
        if not handlers:
            return []
        hits = sorted({m.group(1) for m in _PAYLOAD_DOT_TYPE.finditer(src)} & handlers)
        if not hits:
            return []
        name = hits[0]
        return [
            Finding(
                level=ERROR,
                code="EVENT_PAYLOAD_NOT_NESTED",
                message=f"reads `{name}.type`, but a run event arrives wrapped: "
                f"{{sessionKey, runId, agentId, ts, event}}. The type is `{name}.event.type` "
                f"— as written, every branch misses and the UI silently never updates",
                path=rel,
                fix=f"const ev = {name}.event  — then switch on ev.type, and read ev.kind / "
                f"ev.delta / ev.toolName off that",
            )
        ]

    # ---------------------------------------------------------------- event names
    def _event_vars(self, src: str) -> set[str]:
        """Identifiers that genuinely hold an EVENT — the handler's payload (people do switch
        on `payload.event.type` via an alias) plus anything assigned from `<payload>.event`.

        Scoping matters: `.type` is also the discriminator on stored CONTENT BLOCKS
        (`c.type === 'tool_use'`), on DOM nodes, and on anything else. Flagging every
        `.type === '...'` in the file produced exactly that false alarm, and a rule that cries
        wolf on working code gets ignored — which costs more than the defect it caught.
        """
        payloads = {m.group(1) for m in _HANDLER.finditer(src)}
        payloads |= {m.group(1) for m in _ON_CHAT_EVENT.finditer(src)}
        # a callback passed BY NAME: resolve the name, then take that function's first param
        for m in _HANDLER_REF.finditer(src):
            fn = m.group(1) or m.group(2)
            if not fn:
                continue
            for pm in re.finditer(_FUNC_PARAM.format(name=re.escape(fn)), src):
                param = pm.group(1) or pm.group(2)
                if param:
                    payloads.add(param)
        aliases = {m.group(1) for m in _EVENT_ALIAS.finditer(src) if m.group(2) in payloads}
        return payloads | aliases

    def _literals_compared_as_events(self, src: str) -> set[str]:
        """Every string literal actually tested against an event variable's type/kind."""
        found: set[str] = set()
        for var in self._event_vars(src):
            found |= set(re.findall(_TYPED_COMPARE.pattern.format(var=re.escape(var)), src))
            for m in re.finditer(_SWITCH.pattern.format(var=re.escape(var)), src):
                # the switch body: from the opening brace to the matching close
                depth, i = 0, m.end() - 1
                while i < len(src):
                    if src[i] == "{":
                        depth += 1
                    elif src[i] == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    i += 1
                found |= set(_CASE.findall(src[m.end() : i]))
        return found

    def _unknown_events(self, rel: str, src: str) -> list[Finding]:
        """A name tested as an event type that no event is ever named."""
        known = self._events | self._kinds
        out: list[Finding] = []
        for literal in sorted(self._literals_compared_as_events(src) - known):
            close = self._closest(literal, known)
            out.append(
                Finding(
                    level=ERROR,
                    code="UNKNOWN_EVENT",
                    message=f"compares an event against '{literal}', which no event or kind is "
                    f"ever named — that branch can never run",
                    path=rel,
                    fix=f"did you mean '{close}'? Streamed text is 'message_update' with "
                    f"kind 'text_delta'. The full list is in the build-agent skill, and the "
                    f"definitions in clients/sdk-js/src/protocol.ts",
                )
            )
        return out

    @staticmethod
    def _closest(literal: str, known: frozenset[str]) -> str:
        import difflib

        match = difflib.get_close_matches(literal, sorted(known), n=1, cutoff=0.4)
        return match[0] if match else "message_update"

    # ---------------------------------------------------------------- calls
    def _unknown_calls(self, rel: str, src: str) -> list[Finding]:
        out: list[Finding] = []
        for method in sorted({m.group(1) for m in _RPC_CALL.finditer(src)}):
            if method in self._methods:
                continue
            out.append(
                Finding(
                    level=ERROR,
                    code="METHOD_NOT_APP_CALLABLE",
                    message=f"calls '{method}', which an agent app connection may not use — "
                    f"the daemon refuses it at dispatch",
                    path=rel,
                    fix="use one of the app-tier methods listed in the build-agent skill; "
                    "administration (installs, projects, automation) is host-only",
                )
            )
        if self._sdk:
            for call in sorted({m.group(1) for m in _SDK_CALL.finditer(src)}):
                if call in self._sdk:
                    continue
                out.append(
                    Finding(
                        level=WARN,
                        code="UNKNOWN_SDK_METHOD",
                        message=f"calls client.{call}(), which the vendored SDK does not "
                        f"define — this throws at runtime",
                        path=rel,
                        fix="read ui/vendor/agentd-client.js for the methods it actually has",
                    )
                )
        return out

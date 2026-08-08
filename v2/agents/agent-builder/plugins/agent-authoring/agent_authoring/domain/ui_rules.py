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
                 sdk_methods: frozenset[str]):
        self._events = events
        self._kinds = kinds
        self._methods = methods
        self._sdk = sdk_methods

    def check(self, spec, raw_toml: dict, files: list[str], sources: dict) -> list[Finding]:
        """`sources` maps an agent-relative path -> its text. Only ui/*.js is considered."""
        out: list[Finding] = []
        for rel, src in sorted(sources.items()):
            if not (rel.startswith("ui/") and rel.endswith(".js")):
                continue
            if "/vendor/" in rel:
                continue  # the SDK itself — vendored verbatim, not the agent's code
            # Comments are not code. Without this the rules fire on a file that WARNS about
            # the very mistake they check for — which is the most careful code they will ever
            # see, and exactly the false alarm that gets a check switched off.
            code = _STRIPPER.strip(src)
            out += self._nested_payload(rel, code)
            out += self._unknown_events(rel, code)
            out += self._unknown_calls(rel, code)
        return out

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

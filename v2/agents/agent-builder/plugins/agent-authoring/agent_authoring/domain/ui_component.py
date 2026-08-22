"""UiComponent — a piece of an agent's window that EVERY agent must have, and how to spot it.

This is a catalogue, not a factory. Nothing here reads or writes a file; it says what the
mandatory pieces are, what a wired-up one looks like in source, and which SDK symbols it needs.
`UiRules` is GIVEN it, so "what is required" and "how do I recognise it" have exactly one
definition instead of a regex in the validator and a snippet somewhere else that drift apart.

WHAT THIS USED TO BE. A patcher's descriptor: `<script src>` tags to add to index.html, a CSS
block to append to style.css, a snippet to splice into app.js at an anchor comment. All of that
was for hand-written vanilla `ui/` folders, and it went with them — agent windows are React
projects now, compiled from `app/src/`, and the shared code arrives as copied MODULES
(`templates/_common/`) rather than as snippets woven into somebody's file. What is left is the
part that was never about vanilla: the requirement itself.

THE THREE FACTS EACH COMPONENT CARRIES:

    detect     regexes that mean "this is wired up in this app's source"
    requires   SDK symbols the vendored bundle must export, or the app dies on load
    provides   files the component SHIPS, which are therefore not evidence of their own use

`provides` is the subtle one and it closed a real hole. The React scaffold delivers
`Credits.tsx`, whose whole body calls `mountCreditsPanel`. Scanning every source file for that
call found it inside the definition, so an agent that never rendered `<Credits />` passed the
check that existed to prove it had — a credits page shipped, validated, and invisible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class UiComponent:
    id: str
    title: str
    summary: str
    #: Wired-up in source looks like one of these. Anchored on distinctive call shapes and kept
    #: conservative: a false alarm on working code trains the model to ignore the report, which
    #: costs more than a missed defect.
    detect: tuple[str, ...] = ()
    #: SDK symbols the vendored bundle must export. Absent means a guaranteed "not a function" on
    #: load — a dead window, every time — which is why it is an error rather than a warning.
    requires: tuple[str, ...] = ()
    #: Basenames of files that merely DEFINE this component. Excluded when looking for evidence
    #: that anything USES it; see the module note. Empty for a component that lives entirely in
    #: the SDK, where there is nowhere for its call to hide except wiring.
    provides: tuple[str, ...] = ()

    def present_in(self, code: str) -> bool:
        return any(re.search(pattern, code) for pattern in self.detect)


SIGN_IN = UiComponent(
    id="sign-in",
    title="Hosted sign-in",
    summary=(
        "Lets people use this app on OUR keys instead of pasting their own. Shows a sign-in "
        "panel when the app is running against a hosted platform, and nothing at all otherwise. "
        "Without it, an agent installed from the marketplace fails every model call with a "
        "provider error and nothing on screen explains why."
    ),
    # `signInFirst` is the common module's wrapper; `mountSignInGate` is the SDK call underneath.
    # Either counts — an agent with its own layout may reach past the wrapper, and what is
    # mandatory is that somebody is signed in, not which of our two doors it went through.
    detect=(r"\b(?:signInFirst|mountSignInGate)\s*\(",),
    requires=("mountSignInGate",),
)


CREDITS = UiComponent(
    id="credits",
    title="Credits & billing",
    summary=(
        "Lets the person using this agent see their credit balance and top it up without "
        "leaving the app. The same panel agentd shows, from the same SDK, so every agent's shop "
        "behaves identically. Renders nothing on a build with no accounts service."
    ),
    # Rendered as an element, imported by name, or the SDK called directly. All three are somebody
    # deliberately using it; the shipped file existing is not.
    detect=(
        r"<\s*Credits\b",
        r"\bimport\b[^\n]*\bCredits\b",
        r"\bmountCreditsPanel\s*\(",
    ),
    requires=("mountCreditsPanel",),
    provides=("Credits.tsx",),
)


SETTINGS = UiComponent(
    id="settings",
    title="Settings",
    summary=(
        "The configuration page, identical to the one the assistant's own window shows — same "
        "knobs, same names, same grouping — plus one thing: this agent's values win over the "
        "daemon's, key by key, and every row says which layer it came from. Without it the person "
        "running this agent cannot change its model, its turn limit or its keys from inside it."
    ),
    # Rendered as an element, or imported by name. Both are somebody deliberately putting it on
    # screen; the copied file existing is not (see `provides`).
    # THE SHARED MODULE, BY PATH — not "a component called Settings".
    #
    # This is stricter than the other two on purpose. Credits accepts an agent's own layout because
    # what is mandatory there is that a balance is reachable. Here what is mandatory is that the
    # page is the SAME page: a user configures the assistant, opens an agent, and must not meet
    # somebody's reinterpretation of it with different names for the same knobs.
    #
    # Matching the name alone was tried and was vacuous — one sample passed with a hand-written
    # `components/Settings.tsx` that shared nothing with it but a word. The import path cannot be
    # satisfied by accident, and the copied module does not match itself: its own imports are
    # relative (`./schema`), so shipping the files still proves nothing.
    detect=(r"\bcommon/settings\b",),
    # NO SDK SYMBOL. The page talks to the daemon through `client.request('config.get')`, which
    # every SDK build has ever had — there is no version-sensitive export to check, and inventing
    # one would be a check that can never fail.
    requires=(),
    provides=("Settings.tsx",),
)


class UiComponents:
    """The catalogue.

    Adding a third mandatory piece is an entry here plus a line in `_REQUIRED_MESSAGES` — that is
    the difference between a mechanism and a special case.
    """

    def __init__(self, components: tuple[UiComponent, ...] = (SIGN_IN, CREDITS, SETTINGS)):
        self._by_id = {c.id: c for c in components}

    def ids(self) -> tuple[str, ...]:
        return tuple(self._by_id)

    def all(self) -> tuple[UiComponent, ...]:
        return tuple(self._by_id.values())

    def get(self, component_id: str) -> UiComponent | None:
        return self._by_id.get((component_id or "").strip())

    def describe(self) -> str:
        return "\n".join(f"  {c.id} — {c.summary}" for c in self._by_id.values())

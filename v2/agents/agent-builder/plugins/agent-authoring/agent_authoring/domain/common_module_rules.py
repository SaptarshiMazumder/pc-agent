"""CommonModuleRules — is the agent's copy of the shared modules still the shared modules?

WHAT `app/src/common/` IS. Accounts and money: sign-in, the account menu, the credits page. Every
agent with a window needs all of it, none of it is a judgement about any particular agent, and it
is COPIED into each one at scaffold time rather than imported — because an agent is a shipped
artifact, and a workspace path does not survive being packaged, published and downloaded onto
somebody else's machine.

WHY THAT COPY HAS TO BE CHECKED. Copying is how the code gets there; it is not what keeps it right.
A copy is editable, and the edits are always reasonable at the time — a colour, a label, a
"temporary" change to get past something. What ends up different is credential handling and
payment handling, in an artifact that is then published. Six months on there is no shared module,
only a dozen agents that each look slightly like one, and no way to fix any of them at once.

So this rule says one thing: **the copy must equal the source.** Change the source, re-scaffold or
re-copy, and every agent moves together. Edit the copy and validation says so before it ships.

COMPARED AS NORMALISED TEXT, not bytes. Git checks these files out with CRLF on Windows and LF
elsewhere; a byte comparison would fail on the platform rather than on the content, which is a
rule nobody can satisfy and everybody learns to ignore.

THREE FINDINGS, and they fail differently:

  * MISSING  — a module that never arrived, or was deleted. The agent has no sign-in or no credits
    page at all, and the app will not even build if anything imports it.
  * MODIFIED — it arrived and was then edited. It still builds, which is what makes this the
    dangerous one: nothing looks wrong.
  * NO TOKENS — it arrived intact and the agent never defined the CSS custom properties it reads.

The third one is the other half of the same bargain and was missing for a while. These modules
define no colours and no fonts AT ALL — every visual property is a `var()` — because layout
travels with the module and appearance belongs to the agent. That only works if the agent supplies
the names. When it does not, the page renders structurally perfect and visually blank: transparent
cards, inherited text, no accent, a Save button with no fill. It builds, it validates, and it is
unusable. Exactly the MODIFIED failure mode — nothing looks wrong from the outside — which is why
it is checked the same way.

PURE by construction — the agent's file text comes in, the canonical text comes in, findings come
out. No filesystem, so the whole rule is testable with two dicts.
"""

from __future__ import annotations

import re

from .finding import ERROR, Finding

#: Where the copies live inside an agent, relative to the agent root.
COMMON_DIR = "app/src/common/"


def _normalised(text: str) -> str:
    """Line endings and a trailing newline are not content. Everything else is."""
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


#: `var(--name)` — a token being READ. Deliberately excludes `var(--name, fallback)`: a token with
#: a fallback needs nobody to define it, and reporting one would be a demand the module itself
#: says is optional.
_TOKEN_READ = re.compile(r"var\(\s*(--[A-Za-z0-9_-]+)\s*\)")
#: `--name:` at the start of a declaration — a token being DEFINED.
_TOKEN_DEF = re.compile(r"(--[A-Za-z0-9_-]+)\s*:")


class CommonModuleRules:
    """:param canonical: ``relative path under common/ -> the source text``. INJECTED, because the
    templates directory is the composition root's business — and because a rule that reads its own
    files off disk cannot be tested without staging a tree."""

    def __init__(self, canonical: dict[str, str] | None = None):
        self._canonical = {k: _normalised(v) for k, v in (canonical or {}).items()}

    def check(self, spec, raw_toml: dict, files: list[str], sources: dict) -> list[Finding]:
        # No catalogue to compare against (a build that did not ship the templates) means no
        # opinion. Silence beats inventing a failure out of our own missing data.
        if not self._canonical:
            return []
        # An agent with no window has no app to carry these, and one with no `app/` has not been
        # scaffolded yet. Neither is a mistake, and warning about either is how a check earns its
        # way into being ignored.
        if not isinstance(raw_toml.get("app"), dict):
            return []
        if not any(f.startswith("app/") for f in files):
            return []

        out: list[Finding] = []
        for rel, canonical in sorted(self._canonical.items()):
            path = f"{COMMON_DIR}{rel}"
            text = sources.get(path)
            if text is None:
                out.append(
                    Finding(
                        ERROR,
                        "UI_COMMON_MISSING",
                        f"{path} is missing. It is one of the shared modules every agent gets — "
                        f"accounts and money — and something in this app almost certainly imports "
                        f"it.",
                        path=path,
                        fix="re-copy it from the common modules (templates/_common/). Do not "
                        "write a replacement: the point of the shared copy is that every agent "
                        "handles credentials and payments the same way.",
                    )
                )
                continue
            if _normalised(text) != canonical:
                out.append(
                    Finding(
                        ERROR,
                        "UI_COMMON_MODIFIED",
                        f"{path} has been edited. These modules are copied verbatim into every "
                        f"agent, so a local edit forks credential or payment handling into an "
                        f"artifact that gets published — and it still builds, which is why "
                        f"nothing else would catch it.",
                        path=path,
                        fix="restore it from the common modules (templates/_common/). If the "
                        "change is genuinely needed by every agent, make it THERE so they all "
                        "get it; if it is only about this agent's look, use the CSS custom "
                        "properties each module documents.",
                    )
                )
        out += self._tokens(sources)
        return out

    def _tokens(self, sources: dict) -> list[Finding]:
        """Does the agent define the custom properties its copied modules read?

        WHAT THIS CATCHES is a window with two themes in it. A sample shipped a dark shell and a
        white settings page in the middle of it, because the app had a palette under its own names
        and the modules were reading names nobody had defined. It built, it validated, and the
        balance on the credits page was grey on white.

        BOTH FILES ARE READ TOGETHER — a token may be defined in `tokens.css`, in `styles.css`, or
        by one aliasing the other, and custom properties resolve where they are USED rather than
        where they are written, so there is no ordering to respect. What matters is only that the
        name exists somewhere in the agent's own CSS.

        THE MODULES' OWN CSS IS EXCLUDED from the definitions. They define none of these by
        design; counting them would let a module satisfy its own contract, which is the same hole
        `provides` closes for components.
        """
        consumed: set[str] = set()
        for rel, text in self._canonical.items():
            if rel.endswith(".css"):
                consumed |= set(_TOKEN_READ.findall(text))
        if not consumed:
            return []

        defined: set[str] = set()
        agent_css = False
        for path, text in sources.items():
            if not path.startswith("app/src/") or not path.endswith(".css"):
                continue
            if path.startswith(COMMON_DIR):
                continue
            agent_css = True
            defined |= set(_TOKEN_DEF.findall(text))

        # No stylesheet of the agent's own at all. That is a different problem — an app with no
        # CSS is not an app that got its palette wrong — and naming twenty missing tokens would
        # bury it.
        if not agent_css:
            return []

        missing = sorted(consumed - defined)
        if not missing:
            return []
        shown = ", ".join(missing[:8]) + (f" and {len(missing) - 8} more" if len(missing) > 8 else "")
        return [
            Finding(
                ERROR,
                "UI_TOKENS_MISSING",
                f"the shared modules read {len(missing)} CSS custom propert"
                f"{'y' if len(missing) == 1 else 'ies'} this app never defines ({shown}). They "
                f"define no colours or fonts of their own — every visual property is a var() — so "
                f"the settings, credits and sign-in pages will render with no background, no "
                f"accent and inherited text. It builds and it looks broken.",
                path="app/src/tokens.css",
                fix="define them in `app/src/tokens.css`, imported before your own stylesheet. "
                "The React starter ships one with every name already set; if this agent has its "
                "own palette under different names, map them (`--text: var(--ink)`) rather than "
                "restating the colours — a second copy of a palette only knows about one theme, "
                "and an agent with a dark mode then gets a light settings page inside a dark "
                "window.",
            )
        ]

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

TWO FINDINGS, and they fail differently:

  * MISSING  — a module that never arrived, or was deleted. The agent has no sign-in or no credits
    page at all, and the app will not even build if anything imports it.
  * MODIFIED — it arrived and was then edited. It still builds, which is what makes this the
    dangerous one: nothing looks wrong.

PURE by construction — the agent's file text comes in, the canonical text comes in, findings come
out. No filesystem, so the whole rule is testable with two dicts.
"""

from __future__ import annotations

from .finding import ERROR, Finding

#: Where the copies live inside an agent, relative to the agent root.
COMMON_DIR = "app/src/common/"


def _normalised(text: str) -> str:
    """Line endings and a trailing newline are not content. Everything else is."""
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


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
        return out

"""AgentdIgnore — what an agent declares must never travel: into or out of the command sandbox,
or into a package.

WHY THE AGENT DECLARES IT. What a command leaves behind depends entirely on the command: a
Terraform agent gets `.terraform/` (the AWS provider, ~600 MB), a Node agent `node_modules/`, an
Azure agent something else again. The platform cannot list them without hard-coding every tool
anyone will ever run, so the author lists them, in `.agentdignore` beside `agent.toml`, and Agent
Builder keeps that list current as the agent grows. The platform only ships the language-level
junk every agent has.

WHY IT MATTERS. The workspace is copied into the sandbox before each command and the changes are
copied back after. A re-creatable dependency folder in it is dead weight on every call; once, it
was thousands of files written back onto the shared volume, and the daemon stopped answering its
health check and was killed. And the same file keeps a package clean: anything declared here never
ships.

ONE MATCHER, THREE CALLERS — the daemon zipping a tree in, the executor choosing what to send back,
and the packer. They share this class so a pattern can never mean one thing on the way in and
another on the way out.

THE SYNTAX IS A GITIGNORE SUBSET, because authors already know it:
    # comment          ignored
    name/              a DIRECTORY with that name, at any depth, and everything under it
    *.tfstate          a file or directory NAME, at any depth (fnmatch wildcards)
    build/out.log      a PATH, anchored at the file's own folder
    /cache             anchored, leading slash optional
Negation (`!`) is not supported: an exception to an exclusion is how secrets leak back in.

PURE: text in, answers out. Reading the file is the caller's job.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase

FILENAME = ".agentdignore"


@dataclass(frozen=True)
class _Rule:
    base: str  # folder the patterns are relative to, "" = the tree root, "/"-separated
    pattern: str
    dir_only: bool
    anchored: bool


class AgentdIgnore:
    """A set of ignore rules, each tied to the folder its `.agentdignore` came from."""

    def __init__(self, rules: tuple[_Rule, ...] = ()) -> None:
        self._rules = rules

    # ------------------------------------------------------------------ building

    @staticmethod
    def parse(text: str) -> list[str]:
        """The usable patterns in a file's text, in order."""
        out = []
        for raw in (text or "").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            out.append(line)
        return out

    @classmethod
    def from_files(cls, files: list[tuple[str, str]]) -> "AgentdIgnore":
        """`files` = [(base, text)]: each file's folder relative to the tree root, and its text.
        A file at the root has base ""."""
        rules = []
        for base, text in files:
            b = str(base or "").strip("/").replace("\\", "/")
            for p in cls.parse(text):
                dir_only = p.endswith("/")
                core = p.strip("/")
                if not core:
                    continue
                anchored = p.startswith("/") or "/" in core
                rules.append(_Rule(base=b, pattern=core, dir_only=dir_only, anchored=anchored))
        return cls(tuple(rules))

    @classmethod
    def from_wire(cls, wire: list | None) -> "AgentdIgnore":
        """The shape `to_wire` produces — how the rules cross to the executor."""
        return cls.from_files([(str(w.get("base") or ""), str(w.get("text") or ""))
                               for w in (wire or []) if isinstance(w, dict)])

    def to_wire(self) -> list[dict]:
        by_base: dict[str, list[str]] = {}
        for r in self._rules:
            line = ("/" if r.anchored and "/" not in r.pattern else "") + r.pattern
            by_base.setdefault(r.base, []).append(line + ("/" if r.dir_only else ""))
        return [{"base": b, "text": "\n".join(lines)} for b, lines in by_base.items()]

    def __bool__(self) -> bool:
        return bool(self._rules)

    # ------------------------------------------------------------------ asking

    def matches(self, rel_path: str) -> bool:
        """Is `rel_path` (a FILE, relative to the tree root, "/"-separated) ignored?

        A file is ignored when it, or any folder above it, matches a rule whose base contains it.
        Folder rules therefore take everything under the folder with them."""
        rel = str(rel_path or "").replace("\\", "/").strip("/")
        if not rel:
            return False
        for r in self._rules:
            if r.base:
                if not (rel == r.base or rel.startswith(r.base + "/")):
                    continue
                sub = rel[len(r.base) + 1:] if rel != r.base else ""
            else:
                sub = rel
            if not sub:
                continue
            parts = sub.split("/")
            # Every folder on the way down, and — unless the rule is folder-only — the file itself.
            candidates = range(1, len(parts)) if r.dir_only else range(1, len(parts) + 1)
            for i in candidates:
                if r.anchored:
                    if fnmatchcase("/".join(parts[:i]), r.pattern):
                        return True
                elif fnmatchcase(parts[i - 1], r.pattern):
                    return True
        return False


__all__ = ["AgentdIgnore", "FILENAME"]

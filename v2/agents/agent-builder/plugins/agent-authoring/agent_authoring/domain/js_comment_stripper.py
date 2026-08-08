"""JsCommentStripper — blank out the comments in a JS file so a rule reads only code.

Written because `UiRules` flagged a file for a mistake the file was WARNING about:

    // Reading `payload.type` makes every branch miss and the screen never updates.

A rule that fires on the sentence describing a bug is worse than one that misses the bug —
it is wrong on the most carefully written code it will ever see, and a check that cries wolf
gets switched off. Generated UIs are full of comments; without this, so are the false alarms.

Comments are replaced with SPACES rather than deleted, so every offset in the returned string
still matches the original — a caller can report a position from the stripped text and have it
mean something in the file on disk.

The hard part is not comments, it is knowing when `/` is not one. All three of these appear in
real UI code and none of them starts a comment:

    const url = 'https://example.com'      // a string containing //
    src.split(/`([^`]+)`/)                 // a regex containing a backtick
    `a ${b ? 'x' : 'y'} c`                 // a template literal with a nested expression

So this is a small scanner, not a regex. It tracks strings (all three quotes, with escapes),
template-literal `${}` nesting, and regex literals — the last via the standard heuristic: a
`/` begins a regex only where a VALUE cannot already have ended, i.e. after an operator,
punctuation, or a keyword like `return`. Ambiguity resolves toward "not a comment", because
mistakenly stripping code is how a stripper starts hiding real defects.
"""

from __future__ import annotations

import re

# The last token before `/` that means "a value ends here", so `/` must be division rather
# than the start of a regex literal. Everything else -> regex.
_VALUE_END = re.compile(r"[\w$)\]]$")
# ...except these, which look like identifiers but are keywords a regex can legally follow.
_KEYWORDS = frozenset(
    {
        "return", "typeof", "instanceof", "in", "of", "new", "delete", "void",
        "case", "do", "else", "yield", "await", "throw",
    }
)
_TRAILING_WORD = re.compile(r"([A-Za-z_$][\w$]*)$")


class JsCommentStripper:
    """Replaces comment bodies with spaces. Everything else is returned unchanged."""

    def strip(self, src: str) -> str:
        out = list(src)
        i, n = 0, len(src)
        # ``` `a ${ b } c` ``` — each open `${` pushes; `}` pops back into the literal
        template_depth: list[int] = []
        brace_depth = 0

        while i < n:
            ch = src[i]

            # ---- comments -------------------------------------------------
            if ch == "/" and i + 1 < n:
                nxt = src[i + 1]
                if nxt == "/":
                    while i < n and src[i] != "\n":
                        out[i] = " "
                        i += 1
                    continue
                if nxt == "*":
                    while i < n and not (src[i] == "*" and i + 1 < n and src[i + 1] == "/"):
                        # keep newlines: line numbers in the stripped text must still line up
                        if src[i] != "\n":
                            out[i] = " "
                        i += 1
                    for _ in range(2):  # the closing */
                        if i < n:
                            out[i] = " "
                            i += 1
                    continue
                if self._starts_regex(src, i):
                    i = self._skip_regex(src, i)
                    continue
                i += 1
                continue

            # ---- strings --------------------------------------------------
            if ch in ("'", '"'):
                i = self._skip_quoted(src, i, ch)
                continue

            if ch == "`":
                i = self._skip_template(src, i, template_depth, brace_depth)
                continue

            # ---- braces, so `}` can close a ${ } hole in a template --------
            if ch == "{":
                brace_depth += 1
            elif ch == "}":
                if template_depth and brace_depth == template_depth[-1]:
                    template_depth.pop()
                    i = self._resume_template(src, i + 1, out, template_depth, brace_depth)
                    continue
                brace_depth = max(0, brace_depth - 1)

            i += 1

        return "".join(out)

    # ------------------------------------------------------------------ parts
    @staticmethod
    def _skip_quoted(src: str, i: int, quote: str) -> int:
        """Past a '…' or "…" literal. An unterminated one runs to the end of the line, which
        is what a JS engine would also treat as an error rather than swallowing the file."""
        i += 1
        while i < len(src):
            c = src[i]
            if c == "\\":
                i += 2
                continue
            if c == quote or c == "\n":
                return i + 1
            i += 1
        return i

    def _skip_template(self, src: str, i: int, template_depth: list[int], brace: int) -> int:
        """Past a `…` literal, stopping at the first `${` so the expression inside is scanned
        as code — a comment can legally live in there."""
        i += 1
        while i < len(src):
            c = src[i]
            if c == "\\":
                i += 2
                continue
            if c == "`":
                return i + 1
            if c == "$" and i + 1 < len(src) and src[i + 1] == "{":
                template_depth.append(brace + 1)
                return i + 2
            i += 1
        return i

    def _resume_template(
        self, src: str, i: int, out: list, template_depth: list[int], brace: int
    ) -> int:
        """A `${…}` hole just closed: the rest of the literal continues from here."""
        j = i
        while j < len(src):
            c = src[j]
            if c == "\\":
                j += 2
                continue
            if c == "`":
                return j + 1
            if c == "$" and j + 1 < len(src) and src[j + 1] == "{":
                template_depth.append(brace + 1)
                return j + 2
            j += 1
        return j

    @staticmethod
    def _starts_regex(src: str, i: int) -> bool:
        """Is the `/` at `i` the start of a regex literal rather than division?"""
        before = src[:i].rstrip()
        if not before:
            return True
        word = _TRAILING_WORD.search(before)
        if word and word.group(1) in _KEYWORDS:
            return True
        return not _VALUE_END.search(before)

    @staticmethod
    def _skip_regex(src: str, i: int) -> int:
        """Past a /…/flags literal. `[` opens a character class, in which `/` is literal —
        `/[^/]+/` is valid and must not terminate early."""
        i += 1
        in_class = False
        while i < len(src):
            c = src[i]
            if c == "\\":
                i += 2
                continue
            if c == "\n":
                return i  # unterminated: not a regex after all, resume scanning normally
            if c == "[":
                in_class = True
            elif c == "]":
                in_class = False
            elif c == "/" and not in_class:
                i += 1
                while i < len(src) and src[i].isalpha():  # flags
                    i += 1
                return i
            i += 1
        return i

"""Blank Python comments and docstrings before the source rules read a file.

The sibling of ``js_comment_stripper``, added for the same reason and after the same mistake: a
rule fired on a plugin whose DOCSTRING said "the ``${SECRET}`` path has its own tests". The
warning was about prose describing the correct behaviour — the most carefully written line in the
file — which is exactly the report an author learns to ignore.

WHAT IS REMOVED, and the line is deliberate:

  * ``# comments`` — never executable, never evidence of anything.
  * DOCSTRINGS and other standalone string statements — a string that is an entire statement is
    documentation. Its content is prose about the code, not the code.

WHAT IS KEPT, and this matters more:

  * every string used as a VALUE. ``os.environ["ACME_API_KEY"]`` and
    ``fetch("https://api.acme.com")`` are the true positives these rules exist to find, and
    blanking strings wholesale would turn the whole scan into decoration.

``tokenize`` rather than a regex, because Python string syntax is not regular: an ``#`` inside a
string, an f-string with a ``#`` in its expression, and a triple-quoted block containing both are
all things a regex gets wrong in a way that silently drops real code from the scan.

Line and column structure is PRESERVED (blanked in place, newlines kept) so a finding's position
still points at the right line in the file the author is looking at.
"""

from __future__ import annotations

import io
import tokenize


def _blank(text: str) -> str:
    """Same shape, no content: newlines survive so line numbers do not move."""
    return "".join("\n" if ch == "\n" else " " for ch in text)


class PyCommentStripper:
    """Comments and docstrings out, code and its string VALUES in."""

    def strip(self, source: str) -> str:
        if not source:
            return source
        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
        except (tokenize.TokenError, IndentationError, SyntaxError):
            # Unparseable source is scanned RAW rather than skipped. A plugin that does not
            # tokenize is a plugin that does not import either, so the author has a bigger
            # problem — but silently reporting nothing about it would be the wrong help.
            return source

        lines = source.splitlines(keepends=True)
        out = list(lines)
        # Rebuilt as a flat char buffer would lose the position mapping, so edits are applied to
        # the line list by (row, col) span instead.
        edits: list[tuple[int, int, int, int]] = []

        prev_meaningful = tokenize.NEWLINE  # a module starts "at the beginning of a statement"
        for tok in tokens:
            if tok.type == tokenize.COMMENT:
                edits.append((*tok.start, *tok.end))
                continue
            if tok.type in (tokenize.NL, tokenize.INDENT, tokenize.DEDENT):
                continue
            if tok.type == tokenize.STRING and prev_meaningful in (
                tokenize.NEWLINE,
                tokenize.INDENT,
                tokenize.DEDENT,
                tokenize.ENCODING,
            ):
                # A string at the start of a statement is a docstring or a dead literal. Either
                # way it is prose: nothing reads it at runtime.
                edits.append((*tok.start, *tok.end))
            prev_meaningful = tok.type

        for srow, scol, erow, ecol in edits:
            if srow == erow:
                line = out[srow - 1]
                out[srow - 1] = line[:scol] + _blank(line[scol:ecol]) + line[ecol:]
                continue
            first = out[srow - 1]
            out[srow - 1] = first[:scol] + _blank(first[scol:])
            for row in range(srow + 1, erow):
                out[row - 1] = _blank(out[row - 1])
            last = out[erow - 1]
            out[erow - 1] = _blank(last[:ecol]) + last[ecol:]
        return "".join(out)

"""DocumentChunker — extracted text in, retrievable passages out.

WHY CHUNK AT ALL. Embedding a whole paper gives one vector for forty pages, which matches
everything weakly and nothing precisely. Retrieval wants a passage small enough to be about one
thing and large enough to stand on its own when quoted back.

WHY PARAGRAPHS AND NOT A FIXED WINDOW. A window cuts sentences in half, and half a sentence is
what gets quoted to the user as evidence. Paragraphs are packed up to a budget instead, so a chunk
always ends where the author ended something. A single paragraph longer than the budget is split
on sentence boundaries, and only a sentence longer than the budget is cut mid-text — by then there
is no boundary left to respect.

OVERLAP EXISTS FOR THE CLAIM THAT STRADDLES A SEAM. A conclusion often sits in the last line of
one paragraph and the first of the next; without overlap that pairing is unretrievable.
"""

from __future__ import annotations

import re

#: Characters, not tokens. Tokenizing would mean a tokenizer dependency and a model-specific
#: answer, for a boundary that only needs to be approximately right.
DEFAULT_BUDGET = 1200
DEFAULT_OVERLAP = 200

_PARAGRAPH = re.compile(r"\n\s*\n")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


class DocumentChunker:
    def __init__(self, budget: int = DEFAULT_BUDGET, overlap: int = DEFAULT_OVERLAP):
        if budget <= 0:
            raise ValueError("chunk budget must be positive")
        if overlap >= budget:
            # Overlap at or above the budget means every chunk re-emits its predecessor and the
            # walk never advances. Caught here rather than as a hang at ingest time.
            raise ValueError("chunk overlap must be smaller than the budget")
        self.budget = budget
        self.overlap = overlap

    def split(self, text: str) -> list[str]:
        text = (text or "").strip()
        if not text:
            return []

        chunks: list[str] = []
        current = ""
        for para in (p.strip() for p in _PARAGRAPH.split(text)):
            if not para:
                continue
            for piece in self._fit(para):
                if not current:
                    current = piece
                elif len(current) + 2 + len(piece) <= self.budget:
                    current = f"{current}\n\n{piece}"
                else:
                    chunks.append(current)
                    current = self._carry(current, piece)
        if current:
            chunks.append(current)
        return chunks

    def _fit(self, para: str) -> list[str]:
        """A paragraph, broken down only as far as it has to be to fit the budget."""
        if len(para) <= self.budget:
            return [para]
        out: list[str] = []
        current = ""
        for sentence in _SENTENCE.split(para):
            if len(sentence) > self.budget:
                # No boundary left to respect: hard-cut the runaway sentence.
                if current:
                    out.append(current)
                    current = ""
                out.extend(
                    sentence[i : i + self.budget] for i in range(0, len(sentence), self.budget)
                )
                continue
            if not current:
                current = sentence
            elif len(current) + 1 + len(sentence) <= self.budget:
                current = f"{current} {sentence}"
            else:
                out.append(current)
                current = sentence
        if current:
            out.append(current)
        return out

    def _carry(self, previous: str, nxt: str) -> str:
        """Start the next chunk with the tail of the last one, cut at a sentence boundary so the
        overlap reads as prose rather than starting mid-word."""
        if not self.overlap:
            return nxt
        tail = previous[-self.overlap :]
        parts = _SENTENCE.split(tail, maxsplit=1)
        tail = parts[1] if len(parts) > 1 else tail
        tail = tail.strip()
        if not tail or len(tail) + 2 + len(nxt) > self.budget:
            return nxt
        return f"{tail}\n\n{nxt}"

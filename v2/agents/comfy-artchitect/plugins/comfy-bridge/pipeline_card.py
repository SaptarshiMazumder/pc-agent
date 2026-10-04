"""PipelineCard — the approval card pipeline_present built, and whether the person saw THAT card.

pipeline_present builds the card from the design that was checked; the agent passes it to
`ask_user`. The agent once rewrote it on the way — a new title that dropped the download size, and
a question renamed — and the person approved a card that did not say what mattered. So the card is kept, the daemon keeps
what `ask_user` actually showed (checkpoint `presented.ask`), and nothing installs or runs for the
pipeline's stages unless the two say the same thing. Pure: no files, no clock.
"""

from __future__ import annotations


class PipelineCard:
    def __init__(self, card: dict, stages: list[str] | None = None) -> None:
        self._card = card or {}
        # The stages the card covers, by name — a long card shows several stages on one row, so the
        # rows' names are not the stage names.
        self._stages = set(stages or [str(w.get("name") or "") for w in self._card.get("workflows") or []])

    @property
    def stage_names(self) -> set[str]:
        return set(self._stages)

    def differences(self, shown: dict | None) -> list[str]:
        """What the card the person saw (ask_user's normalised ask) changed. [] = the same card."""
        shown = shown or {}
        out = []
        if _t(shown.get("title")) != _t(self._card.get("title")):
            out.append("its title (which carries what it delivers and the download size)")
        if _rows(shown.get("workflows"), "name", "does") != _rows(self._card.get("workflows"), "name", "does"):
            out.append("its steps")
        if _rows(shown.get("questions"), "question", "default") != _rows(self._card.get("questions"), "question", "default"):
            out.append("its questions or the prompts in them")
        if _rows(shown.get("references"), "role", "what") != _rows(self._card.get("references"), "role", "what"):
            out.append("the files the person adds")
        if shown.get("services"):
            out.append("it lists paid services — the design is free models only")
        return out


def _t(value) -> str:
    return " ".join(str(value or "").split())


def _rows(rows, *keys: str) -> list[tuple]:
    return [tuple(_t(r.get(k)) for k in keys) for r in rows or [] if isinstance(r, dict)]


__all__ = ["PipelineCard"]

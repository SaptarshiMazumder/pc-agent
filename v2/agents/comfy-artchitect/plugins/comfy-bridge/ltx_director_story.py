"""LtxDirectorStory — an LTXDirector node's storyboard, read and rewritten as ONE thing.

THE NODE KEEPS ITS STORY THREE TIMES, and they must agree:
    local_prompts     every segment's prompt, joined by " | "
    segment_lengths   each segment's span in frames, comma-separated, from one segment's start to
                      the next (the first from frame 0, the last to the end) — they sum to
                      duration_frames
    timeline_data     the editor's JSON: per segment its type (image keyframe or text), prompt,
                      start and length, and an image segment's file (`imageFile`)
Edited one at a time — the prompts in one place, the lengths in another — the node renders a story
that is neither. This is the only writer: prompts and lengths go in together, the keyframes' files
and types stay where they are, the total length follows the lengths.

Pure: a node's inputs in, new inputs out.
"""

from __future__ import annotations

import copy
import json

SEPARATOR = " | "


class LtxDirectorStory:
    def __init__(self, inputs: dict) -> None:
        self._inputs = inputs
        try:
            self._timeline = json.loads(str(inputs.get("timeline_data") or "{}"))
        except ValueError as e:
            raise ValueError(f"timeline_data is not JSON: {e}") from e
        self._segments = list(self._timeline.get("segments") or [])
        if not self._segments:
            raise ValueError("the storyboard has no segments")

    def segments(self) -> list[dict]:
        """[{n, type, prompt, frames}] in order — what the agent reads before rewriting."""
        spans = self._spans()
        return [{"n": i + 1, "type": str(s.get("type") or ""), "prompt": str(s.get("prompt") or ""),
                 "frames": spans[i]} for i, s in enumerate(self._segments)]

    def write(self, prompts: list[str], frames: list[int] | None = None) -> dict:
        """The node's inputs with the story replaced. Raises ValueError, in words, on a story that
        does not fit: a different number of segments, a prompt with the separator, a bad length."""
        n = len(self._segments)
        if len(prompts) != n:
            raise ValueError(f"the storyboard has {n} segments ({self._shape()}) — give {n} prompts, in order")
        prompts = [" ".join(str(p).split()) for p in prompts]
        if any(not p for p in prompts):
            raise ValueError("every segment needs a prompt")
        if any("|" in p for p in prompts):
            raise ValueError("a prompt may not contain '|' — the node joins its segments with it")
        spans = self._spans() if frames is None else [int(f) for f in frames]
        if len(spans) != n or any(f < 1 for f in spans):
            raise ValueError(f"give {n} segment lengths in frames, each at least 1")
        out = copy.deepcopy(self._inputs)
        timeline = copy.deepcopy(self._timeline)
        start = 0
        for seg, prompt, span in zip(timeline["segments"], prompts, spans):
            seg["prompt"] = prompt
            if frames is not None:
                seg["start"], seg["length"] = start, span
            start += span
        out["timeline_data"] = json.dumps(timeline, ensure_ascii=False)
        out["local_prompts"] = SEPARATOR.join(prompts)
        out["segment_lengths"] = ",".join(str(s) for s in spans)
        if frames is not None:
            total = sum(spans)
            out["duration_frames"] = total
            rate = out.get("frame_rate")
            if isinstance(rate, (int, float)) and rate > 0:
                out["duration_seconds"] = round(total / rate, 3)
        return out

    def _spans(self) -> list[int]:
        raw = str(self._inputs.get("segment_lengths") or "")
        try:
            spans = [int(x) for x in raw.split(",") if x.strip()]
        except ValueError:
            spans = []
        if len(spans) == len(self._segments):
            return spans
        return [int(s.get("length") or 1) for s in self._segments]

    def _shape(self) -> str:
        return ", ".join(f"{i + 1} {s.get('type') or '?'}" for i, s in enumerate(self._segments))


__all__ = ["LtxDirectorStory"]

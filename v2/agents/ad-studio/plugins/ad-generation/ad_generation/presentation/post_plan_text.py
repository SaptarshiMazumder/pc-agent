"""A post's plan as the text a tool returns: each slide, its words, the caption, and the questions
the agent should ask the user."""

from __future__ import annotations

from ad_generation.domain.post import Post


class PostPlanText:
    @staticmethod
    def of(post: Post, questions: list[str]) -> str:
        lines = [f"post '{post.slug}' ({post.format}, {len(post.slides)} slides):"]
        for n, s in enumerate(post.slides, 1):
            words = " / ".join(c.text for c in s.cues) or "no text"
            cut = f", {s.edit.speed}x" if s.kind == "video" and s.edit.speed != 1 else ""
            lines.append(f"{n:02d}. {s.kind} {s.item.rsplit('/', 1)[-1]}{cut} — {words}")
        lines.append("caption:\n" + post.caption)
        if post.hashtags:
            lines.append(" ".join(f"#{h}" for h in post.hashtags))
        if questions:
            lines.append("ASK THE USER: " + " | ".join(questions))
        return "\n".join(lines)

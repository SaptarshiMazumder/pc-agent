"""A story as the agent reads it in a tool result: the direction in a few lines, then the shot list
row by row with what is still to choose, then the questions to ask."""

from __future__ import annotations

from ad_generation.domain.story import Story


class StoryText:
    @staticmethod
    def of(story: Story, questions: list[str] | None = None) -> str:
        lines = [
            f"story {story.id} (v{story.version}, {story.status}): {story.title}",
            f"for: {story.audience or '—'} · occasion: {story.occasion or 'any time'} · goal: {story.goal or '—'} · format: {story.format}",
            f"offer: {story.offer or '—'} · tone: {story.tone or '—'} · language: {story.language or '—'} · who is in it: {story.cast or 'not chosen'}",
            f"hook: {story.hook or '—'}",
            "shots:",
        ]
        for s in story.shots:
            size = f"{s.count or '?'} × {s.kind} {s.aspect or '?'}" + (f" {s.seconds or '?'}s" if s.kind == "video" else "")
            todo = f"  [to choose: {', '.join(s.unmade())}]" if s.unmade() else ""
            text = f' · text "{s.text}"' if s.text else ""
            text += f" · starts from {s.start_from}" if s.start_from else ""
            lines.append(f"  {s.id}. {s.purpose} — {s.shows} · product {s.product or 'none'} · {size}{text}{todo}")
        lines += [f"caption: {story.caption_angle or '—'} · call to action: {story.cta or '—'}"]
        if story.unmade() and not story.auto_pick:
            lines.append("NEXT: the user chooses the counts, shapes and lengths marked above (studio, or tell you), then approves.")
        elif story.status == "draft":
            lines.append("NEXT: show the story to the user in a few lines and wait for their OK or changes — nothing is made before approval.")
        if questions:
            lines.append("ASK the user: " + " | ".join(questions))
        return "\n".join(lines)

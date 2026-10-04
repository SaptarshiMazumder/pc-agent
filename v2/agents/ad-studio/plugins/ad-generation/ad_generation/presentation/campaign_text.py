"""How a step's result reads to the agent: the checklist at a glance, what this run made (with the
checker's advice), and what to do next. Small previews of the new results ride as artifacts, so
they show in the chat without another call — previews, because an attachment is re-sent to the
model on every later turn, and full-size generations made those requests fail."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import ToolResult

_MARK = {"done": "x", "todo": " ", "skipped": "-"}


class CampaignText:
    @staticmethod
    def checklist(detail: dict) -> list[str]:
        lines = [
            f"campaign {detail['campaign_id']} · {detail['product']['name']} · recipe {detail['recipe_key']}"
            + (f" · cast {detail['cast']['name']}" if detail.get("cast") else "")
            + f" · spent ${detail['spent_usd']:.2f} of ${detail['budget_usd']:.2f}"
        ]
        for s in detail["steps"]:
            line = f"  [{_MARK[s['status']]}] {s['id']} — {s['title']} ({s['action']}, {len(s['results'])} results)"
            if s["pick"]:
                line += f" · picked {s['pick']}"
            if s.get("stale"):
                line += " · made from an older pick of " + s["source"]
            lines.append(line)
        return lines

    @staticmethod
    def result(detail: dict, step_id: str, new: list[str], attach: list[str] | None = None) -> ToolResult:
        """`attach`: what rides in the chat (previews of `new`); default nothing."""
        step = next(s for s in detail["steps"] if s["id"] == step_id)
        lines = CampaignText.checklist(detail)
        if step["action"] == "brief" and detail.get("brief"):
            brief = detail["brief"]
            lines += [f"concept: {brief['concept']}", f"hook: {brief['hook']}"]
            lines.append("look: " + "; ".join(f"{k}: {v}" for k, v in brief["look"].items()))
            for shot in brief["shots"]:
                lines.append(f"scene {shot['id']}: {shot['keyframe_prompt']}")
                lines.append(f"   motion: {shot['motion_prompt']}")
        by_path = {r["path"]: r for r in step["results"]}
        for path in new:
            r = by_path.get(path) or {}
            score = f"{r['score']}/10" if r.get("score") is not None else "not checked"
            advice = "" if r.get("passed") in (None, True) else " — checker: " + "; ".join(r.get("problems") or [])
            lines.append(f"made {path} · {r.get('provider', '')}/{r.get('model', '')} · ${r.get('cost_usd', 0):.3f} · {score}{advice}")
        current = detail.get("current") or ""
        lines.append(
            "NEXT: tell the user in a line or two what was made (the checker's score is advice — "
            "they decide), then end the turn. "
            + (f"The next step is '{current}'; run it when the user says." if current else "Every step is done.")
        )
        return ToolResult.text("\n".join(lines), details={"campaign": detail, "made": new}, artifacts=attach or [])

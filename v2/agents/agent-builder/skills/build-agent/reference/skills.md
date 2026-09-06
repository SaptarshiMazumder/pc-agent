# Reference — `skills/`

How to write a playbook for the agent you are building. Prefer one of these over a private
tool: it is markdown, it needs no code, and it is re-read every turn.

---

## skills/ — playbooks

`agents/<id>/skills/<skill-name>/SKILL.md`. An agent sees the global library
(`agents/main/skills/`) **plus** its own; a same-named own skill overrides the global one.

```markdown
---
name: monthly-report
description: Use when the user asks for a monthly spending summary or chart.
always: false
requires_bins: ffmpeg # optional gates — skill hidden unless satisfied
requires_env: SOME_API_KEY
requires_config: memory_enabled
---

# Monthly report

1. Read every CSV under bank/ and cards/.
2. Dedupe on (date, amount, merchant).
   ...
```

`always: true` inlines the full body **every turn** — use only for short routing rules.
Everything else stays `false` and is read on demand. Write the description as a
_trigger condition_ ("Use when…"), because that line is all the model sees before choosing.

**Skills are re-read every turn.** A new SKILL.md takes effect on the next message — no
reload, no restart.


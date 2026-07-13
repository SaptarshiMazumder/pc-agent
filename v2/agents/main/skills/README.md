# Skills

Drop-in **playbooks** for the agent. A skill teaches the agent *how* to do a
specific task well — it is knowledge/instructions, **not** a callable tool. The
agent reads a skill on demand (with its `read` tool) only when your request
matches the skill's description, so you can add as many as you like without
slowing anything down or bloating the prompt.

> Tools = actions the agent can *do* (read, exec, browser).
> Skills = instructions for *how* to do a task with those tools.

## Add a skill

Create one folder per skill, each containing a `SKILL.md`:

```
skills/
  browser-automation/        <- ships with agentd (ported from OpenClaw)
    SKILL.md
  your-skill/                <- add your own the same way
    SKILL.md
    (optional: scripts, templates, assets the playbook refers to)
```

A new skill is picked up on your **next message** — no restart needed.

## SKILL.md format

```markdown
---
name: your-skill
description: Use when <the exact situation this playbook applies to>.
---

# Your Skill

Step-by-step playbook the agent follows once it reads this file.

## When to use
- ...

## Steps
1. ...
2. ...

## Pitfalls
- ...
```

- **Frontmatter** (`---` fenced) needs `description` (one line: *when* to use the
  skill — this is what the agent matches against your request). `name` is
  optional; it defaults to the folder name.
- **Body** is free-form markdown — write it like instructions to a capable
  assistant. Reference any bundled files by relative path; the agent can `read`
  them too.

## Override the location

By default skills live here (`v2/skills/`). Point elsewhere with:

```
AGENTD_SKILLS_DIR=/path/to/your/skills
```

See `browser-automation/SKILL.md` for a real, working skill to model yours on.

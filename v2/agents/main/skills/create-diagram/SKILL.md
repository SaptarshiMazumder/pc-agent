---
name: create-diagram
description: Use to design and render a diagram (architecture, sequence, class, flow/activity, ER, state, gantt, mindmap, ...) as a PNG. Good for explaining a system, process, or structure. Renders via the `plantuml` tool.
requires_bins: java
---

# Create a diagram

Author the diagram as PlantUML, then render it to a crisp PNG with the **`plantuml`** tool.

## Work fast, then refine (don't over-research)
- **Time-box the reading.** Read just enough to get the structure right — a handful of key files,
  not the whole codebase. If the project already has an **architecture doc or an existing diagram,
  START FROM IT** (read that first; adapt it) instead of reconstructing from scratch.
- **Draw a FIRST version early** with the `plantuml` tool, *then* look at the PNG and refine. The
  render is your feedback loop — iterating on the drawing beats reading ten more files. A good rough
  diagram now is better than a perfect mental model that never gets drawn.

## Steps

1. **Pick the type** that fits the idea:
   - sequence — a flow of messages over time
   - component / deployment — architecture, services, boundaries
   - class — structure and relationships
   - activity / state — a process or lifecycle
   - ER — data model
   - mindmap / WBS — a breakdown
   - gantt — a schedule
2. **Write PlantUML source.** Keep labels short; group with `package`/`rectangle`; use colour and
   `note` sparingly for emphasis. `left to right direction` often reads better for wide architectures.
3. **Render** with the `plantuml` tool: pass `source` (or `puml_path`) and `out_path`. It returns the
   PNG path and its pixel width/height, and handles PlantUML's 4096px clip problem for you.
4. **Non-Latin labels** (Japanese / Chinese / Korean): pass `font` (e.g. `"Yu Gothic UI"`) so the text
   isn't rendered as tofu boxes.
5. **LOOK at the PNG.** If a box is cut off, text overflows, or the layout is messy, fix the source and
   re-render. Never ship a diagram you haven't actually viewed.

## For zoom / explainer use

If the diagram will be zoomed (e.g. in a video), keep the returned **width/height** — focus boxes are
fractions of those pixels, so re-check them whenever you re-render and the dimensions change.

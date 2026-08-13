---
name: create-scientific-figure
description: Create a publication-grade scientific figure from a prompt, paper/PDF, sketch, or reference image. Pick an ART TEMPLATE (the style gallery — biorender-shaded, ghosted-anatomy, isometric-3d-stem, flat-vector, watercolor-atlas, …), render textless artwork with Nano Banana Pro, then add labels, leaders, arrows, and an illustrated flowchart as an EDITABLE vector layer (the BioRender / FigureLabs look). Produces editable SVG / PPTX / vector PDF / PNG.
---

# Create a scientific figure

Turn a subject into a finished figure. The craft is: **(1) pick the right art template**, **(2)
render clean TEXTLESS artwork from it with Nano Banana Pro**, and **(3) add every label, arrow, and
flowchart node as an EDITABLE vector layer** — not baked into pixels.

## The tools (each does ONE job)

| tool                              | does                                                                                                                                                                                                                                                                |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `list_templates`                  | browse the ART-TEMPLATE gallery (styles + when to use). Pick an `id`. The gallery is user-extensible (files in `templates/`).                                                                                                                                       |
| `generate_artwork`                | textless raster illustration via **Nano Banana Pro (Gemini 3 Pro Image)**. Pass `template=<id>` + `subject` (+ optional `palette`). Returns the PNG **and the palette to reuse**. |
| `find_reference_image`            | keyless web image search → download candidate reference images to **look at & pick**, then feed to `generate_artwork` `reference_images` for accuracy grounding.                                                                                                    |
| `read_labels_from_image`                     | A LABELLED oracle (an EDIT of your textless art) → ready-to-draw `annotation` elements by READING its drawn labels: text + pointer tip + label position (`at`). |
| `layout_flowchart`                     | nodes + edges → node positions + routed connector waypoints (for a flowchart or a pathway).                                                                                                                                                                         |
| `render_editable_overlay`                  | high-level spec → **editable** SVG: labels, pill labels, leader callouts, premium arrows, **`node` flowchart boxes with embedded icons + step badges**, panels.                                                                                                     |
| `compose_figure_layers`                  | artwork + overlay → flattened PNG **and** layered editable SVG (artwork `<image>` + live `<text>`/`<path>`/`<image>` icons).                                                                                                                                        |
| `verify_figure`                   | image + expected structures → `{ok, missing, extra, wrong, notes}` (the correctness gate).                                                                                                                                      |
| `validate_svg`                    | parse + inventory an SVG (labels/arrows/nodes/embedded images).                                                                                                                                                                                                     |
| `plantuml`                        | diagram code → PNG, for a purely logical/structured diagram.                                                                                                                                                                                |
| `export_pptx` / `export_pdf`      | fully-editable PowerPoint / vector PDF.                                                                                                                                                     |
| `trace_image`                     | Geometric shape-tracing for the separate **vectorize-figure** job (only when the user explicitly asks to trace artwork into vector shapes). Lossy; never a default.                                                                                                                                                                  |

## Routes

| If the figure is… | Route | Why |
|---|---|---|
| a rich labelled scene: anatomy, cell, apparatus | **Native-label** | Nano Banana places labels; we re-draw them as editable overlay over textless art |
| a scene **plus a process flowchart** | **Composite** | one template/palette across art + illustrated flowchart |
| purely logical: flow, hierarchy, ER, network | **Structured** | PlantUML makes it exact |
| a rich painted scene with **no/few labels** | **Illustrated** | one generation, fast |

## Native-label (the default)

1. **Textless base `T`:** `generate_artwork` with `template=<id>` + `subject`, `allow_text: false`.
2. **Labelled ORACLE `L` — an EDIT of `T`:** `generate_artwork` with `reference_images=[T]`, `conditioning: "layout"`, `allow_text: true`.
3. **Read the placements:** `read_labels_from_image`, `image=L`, `base=T`, `structures=<list>`.
4. **Draw the editable layer:** `render_editable_overlay`, `width`/`height` = `T`'s size.
5. **Composite:** `compose_figure_layers` with `artwork=T`.
6. **LOOK** at the flattened PNG. Fix if needed.

## Accuracy

- Never trust raw generation — always run `verify_figure`.
- Ground on references (`find_reference_image`) for anatomy/structure you're not 100% sure of.
- Textless artwork + vector labels removes the entire garbled-label error class.
- Never ship a figure you haven't looked at.

## Canvas annotations

Users can send canvas-edited images from the app. Treat their marks as instructions:
- **Circle / ellipse** → "focus here"
- **Arrow** → "this connects to that"
- **Highlight / mark** → "emphasize this region"
- **Cross-out / scribble** → "remove this"

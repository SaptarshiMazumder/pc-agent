# Art templates — the style gallery

Each `*.toml` in this folder is one **art template**: a reusable, art-directed style recipe the
figure-creator picks from (like FigureLabs' style gallery). Templates are **data, not code** — drop a
new `.toml` here and it appears in `list_templates` and can be used by `generate_artwork` immediately.
No Python change, no restart-of-logic required.

The `generate_artwork` tool takes `template = "<id>"` and a subject; the engine merges the subject
into the template's prompt, applies the palette, conditions on any exemplar images, and sets the
model/aspect/resolution — then renders with Nano Banana Pro (Gemini 3 Pro Image).

## Fields

| field            | required | meaning |
| ---------------- | -------- | ------- |
| `id`             | ✅       | unique slug (matches the filename) — how `generate_artwork` selects it |
| `name`           | ✅       | human title shown in the gallery |
| `description`    | ✅       | one-line summary for the gallery |
| `when_to_use`    | ✅       | guidance the agent reads to pick the right template |
| `prompt`         | ✅       | the rich style-direction text; **must contain `{subject}`** (replaced with what to depict) |
| `tags`           |          | keywords for discovery |
| `palette`        |          | suggested hex colours for cohesion; the agent may override per subject |
| `palette_locked` |          | `true` = force "use ONLY these colours"; `false`/omitted = palette is guidance |
| `negative`       |          | what to exclude (appended as a "do NOT include…" directive) |
| `aspect`         |          | default aspect ratio, e.g. `"4:3"`, `"1:1"`, `"16:9"` |
| `resolution`     |          | default size hint: `"1K"` | `"2K"` | `"4K"` |
| `provider`       |          | image backend (default `gemini`) |
| `model`          |          | image model (default `gemini/gemini-3-pro-image`) |
| `exemplars`      |          | paths (relative to this folder) to gold reference images, passed as **style conditioning** — the strongest lever for a repeatable look |

## Textless by rule

Every template is rendered **textless** — labels, leaders, arrows, and titles are added afterward as
an **editable vector overlay** (that is what makes the figure both correct and editable). Do not write
label text into a template prompt.

## Exemplars — how to make a style repeatable

A text prompt alone rolls the dice. To pin a style so it comes out the same every time:

1. Generate one or two gold-standard images in the target style.
2. Save them in this folder (e.g. `exemplars/ghosted-anatomy-1.png`).
3. List them under `exemplars = [...]`.

From then on, every generation with that template is conditioned on those images (`conditioning =
style`) and inherits the look. Exemplars are optional — templates work without them, better with them.

## Add your own

Copy any file here, change the `id`/filename, rewrite the `prompt` at this level of specificity
(lighting, material, shading approach, palette, background, composition), and it's live. That is the
whole "I want my own art templates" workflow.
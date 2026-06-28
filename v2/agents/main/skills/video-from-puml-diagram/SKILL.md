---
name: video-from-puml-diagram
description: Make a narrated VIDEO from a PlantUML diagram — render the diagram, move the camera across it (zoom/pan into each part) while narrating each, and stitch into an mp4. The diagram is the spine; optionally add a title slide or footage. For EXPLAINING a system, codebase, concept, or process — NOT filmed, stock, or AI-generated footage. Any language(s), one or more speaker voices.
requires_bins: ffmpeg
---

# Video from a PlantUML diagram

Make a narrated VIDEO built around a **PlantUML diagram**: render the diagram, then move the camera
across it — zooming into each part while the narration explains it — and stitch it into an mp4. The
diagram is the **spine** of the video; you can also drop in a title slide or some footage where it
helps.

This is for EXPLAINING something (a system, a codebase, a concept, a process) — it is **NOT** filmed,
stock, or AI-generated video (e.g. "a video of a forest").

**You generate all content from the source every time — never reuse example wording, titles, or a
previous job's content.**

You orchestrate single-purpose tools (each does ONE job):

| tool           | does                                                                        |
| -------------- | --------------------------------------------------------------------------- |
| `plantuml`     | diagram source → PNG (+ pixel dims). See the **create-diagram** skill.      |
| `render_html`  | HTML → PNG/PDF — slide frames, title cards, anything HTML.                  |
| `tts`          | text → narration audio (+ duration). Different `voice` = different speaker. |
| `stitch_video` | ordered segments (image/clip + audio + zoom/trim) → mp4.                    |
| `make_pptx`    | slides spec → .pptx (a deck instead of / alongside the video).              |

## Workflow

### 1. Understand & storyboard

Read the source. Decide the scenes: which are slides, which are diagrams (and exactly what to zoom
into), whether there's footage. Confirm format(s), language(s), speaker(s), depth, audience.

### 2. Build the visuals

- **Diagrams**: write PlantUML → render with `plantuml` → PNG. Keep each PNG's width/height.
- **Slides**: write a self-contained HTML slide (inline CSS, 1920×1080) → render with `render_html` →
  PNG. Design the look to fit THIS subject; for CJK use a font stack with a CJK face. (HTML is generic —
  use `render_html` for title cards, lower-thirds, anything visual too.)

### 3. Narrate

For each beat, call `tts` with the text → audio + duration. Pick `voice` per language/speaker (scenes
can use different voices). If a term might be mispronounced, synth a one-line sample first and listen;
fix by respelling it phonetically **in the narration text only** (e.g. Japanese katakana) — never in
the visuals.

### 4. Frame the zooms

For a diagram beat, choose a `focus` box `[x, y, w, h]` as fractions of that PNG. **Verify it** by
cropping that region and looking (e.g. an ffmpeg crop) before committing — don't guess. Re-check after
any re-render that changes the PNG's dimensions.

### 5. Stitch

Call `stitch_video` with the ordered segments:

- diagram beat → `{ image: diagram.png, audio: beat.mp3, zoom: { from:[x,y,w,h], to:[x,y,w,h] } }`
- slide beat → `{ image: slide.png, audio: beat.mp3 }` (no zoom = still)
- footage beat → `{ clip: footage.mp4, audio: beat.mp3, trim:[start,end] }`

→ `out.mp4`. Tune `zoom_scale` (lower = looser zoom) and `move_frac` to taste.

### 6. Multiple languages

Redo `tts` per language and stitch to a SEPARATE output (e.g. `out.ja.mp4`). Translate the visuals too
(diagram `font`, slide HTML). Keep each language's files separate — never overwrite another's.

### 7. Review before the big render

Preview diagrams, slides, and short audio samples; verify each focus crop. Assemble the full video only
once the pieces look and sound right. **Confirm with the user before publishing or sending anything.**

## Deck instead of (or alongside) video

For a `.pptx`: build the diagrams/visuals the same way, then call `make_pptx` — one slide per point
(title + bullets + the diagram image), with the narration text dropped into each slide's speaker notes.

# Figure-Creator vs FigureLabs.ai — Research Report & Redesign Plan

*Research date: 2026-07-09. Sources: full codebase audit of the figure pipeline + a 100-agent
adversarially-verified web research pass over figurelabs.ai, BioRender AI, and SOTA vectorization
literature + an implementation-stack validation pass. All FigureLabs claims below survived 3-vote
verification against live site fetches (2026-07-09) unless marked otherwise.*

---

## 1. Executive summary

**FigureLabs is not ahead of us on generation — it is ahead of us on what happens *after*
generation.** It has **no proprietary image model**: it is a tier-gated wrapper over the same
third-party raster models we use (Nano Banana 2/Pro, GPT Image 2/1.5, Sora, SeeDream 4.5/5.0,
Flux.2 Max). Its only claimed proprietary component is the **"AI Vectorizer"** — a raster→vector
post-processor (billed at 150 credits, 3× the cost of a generation — their premium step). Their
pipeline is exactly our hybrid strategy: raster-first, vectorize later.

The quality gap the user experiences has four root causes, all now located precisely in our code:

1. **We actively suppress the model's composition intelligence.** Our skill forbids multi-panel
   ("pick exactly ONE route") and strips ALL arrows/callouts from the artwork (`_NO_TEXT`).
   FigureLabs just lets Nano Banana draw the whole figure — labels, arrows, panels — which is why
   it "instinctively" produced cross-section + fuel-flow side by side and we produced one panel.
2. **We throw away Nano Banana's annotation work and re-synthesize it from weak signals.** The
   oracle→VLM-coordinate-readback→redraw chain loses arrows entirely (only label/leader pairs are
   read), misplaces labels (unverified oracle alignment + snap-to-any-ink), and only ever emits
   `annotation` elements (leader + dot) — even though our SVG engine already supports curved
   routes, tapered bodies, and five arrowhead styles that nothing ever exercises.
3. **There is no edit system.** No region-redraw, no text-edit, no lineage manifest linking the
   delivered figure to its layers — so "fix the nozzle" has no defined path. FigureLabs sells
   exactly these as discrete ops: Text Edit (60cr), Region Redraw (50cr), Regenerate (50cr),
   White BG (50cr) + vectorize-then-edit-in-canvas.
4. **Verification is optional prose.** `verify_figure` exists but no code gate forces it; the
   white-bg/no-shadow invariant is prompt-only and never measured.

**The fix is one architectural move plus three subsystems** (§5): switch the default route to
*generate-labeled-then-extract* (diff the labeled image against its stripped version, OCR the text,
trace the arrow strokes, re-emit through our existing SVG engine — with extraction running
**lazily**: the labeled PNG is the default deliverable, SVG only on user request / Edit button),
add a **Figure Spec planning step** (multi-panel decomposition), add an **edit router + figure
manifest**, and make **verification a gate**. Everything reuses the engine we already built; the new code is mostly
deterministic pixel work with tiny, verified dependencies.

Constraints honored per user direction: **1K generation stays for now**; the **strip-labels step is
trusted** as-is.

---

## 2. FigureLabs.ai — verified capability map

### 2.1 Generation (all third-party models, tier-gated)

- **Text-to-Figure** — from text *or PDFs* (parse a paper, propose the figure).
- **Image-to-Figure / Sketch-to-Figure** — hand-drawn sketches or photos → publication-style art.
- **Reference-to-Figure** — match style/layout of any reference image (newer; tutorial slots for
  "Reference to Image" and "Photo to Figure" exist unreleased).
- Models: free tier = Nano Banana Pro + GPT Image 2; paid adds Sora, Flux.2 Max, SeeDream.
  Uniform generation price (50 credits ≈ $0.0x) regardless of model.
- Domains marketed: Life Sciences, Engineering, Physics, Chemistry, CS (neural-net architectures,
  mechanical systems, experimental setups, 3D material structures). "Trained on data across…" is
  marketing — generation is stock third-party models.

### 2.2 Editing ("fix this") — two paths, each a discrete billed op

- **Raster path (before vectorizing):** Text Edit — fix labels/legends directly on the image
  (60cr); Region Redraw — redraw only a selected region (50cr); Regenerate (50cr); White BG /
  BG Remove (50cr). Plus "Figure Refiner" (press-release only, 2-1 vote): upscale, color
  correction, denoise — works on uploaded figures too.
- **Vector path (after vectorizing):** tweak elements in a built-in vector canvas, or export
  SVG/PPTX and finish in Illustrator / PowerPoint / BioRender.

### 2.3 Vectorization & export (their moat, and their weak point)

- Proprietary **AI Vectorizer** (internal feature key literally `rasterToVector`), 150 credits.
  Marketing: "100% clean, layered SVGs". **No verified evidence of what it actually emits** —
  semantic objects vs traced path soup is THE open competitive question (§8). Their own examples
  show slightly inaccurate vector label positioning (user's observation), consistent with a
  trace + OCR reconstruction, not a knowledge-driven redraw.
- Exports: SVG, editable PPTX, PNG/JPG upscaled to 8K (~1200 DPI; 8K gated to higher tiers).
  Upscale 2K/4K/8K = 10/20/40cr.
- Separate **Research Flowcharts** product emits native SVG/PPTX directly (like our
  plantuml/layout_flowchart route).

### 2.4 Pricing & positioning

- From $9/month; free = 150 signup credits + 50/day. Self-positions as **"The BioRender
  Companion"** — generate fast here, finish layout in BioRender/Illustrator/PPT.
- **BioRender AI** (incumbent) is split-architecture: its AI-image figures are **flat PNGs, no
  SVG export at all** (their help docs confirm), while its text-to-flowchart/protocol/timeline
  tools compose from BioRender's native component library (fully editable, but biology-only
  assets). FigureLabs attacks the flat-PNG weakness; we can attack both (we already emit layered
  SVG with live text + real PPTX shapes).

### 2.5 What FigureLabs does NOT appear to have

No evidence of: a planning/decomposition step (multi-panel comes from the raster model itself),
automatic label placement algorithms, VLM grounding, per-domain templates, or an accuracy/verify
step. Their accuracy edge in practice comes from (a) not fighting the model's native composition
and (b) newer models (NB 2 vs our NB "3-pro-image" config — check model currency), not from a
knowledge engine.

---

## 3. Our pipeline today (from the code audit)

Happy path: `generate_artwork(template, subject, allow_text:false)` = textless **T** →
second `generate_artwork(reference_images=[T], conditioning:layout, allow_text:true, "add
labels + thin straight leader lines")` = oracle **L** → `read_labels_from_image(image=L, base=T)`
(Gemini reads label text + leader endpoints as 0-1000 [y,x]; snap-to-ink) → `annotation` elements →
`render_editable_overlay` → `compose_figure_layers` (raster `<image>` + live `<text>`/`<path>`) →
optional `verify_figure` → `export_pptx`/`export_pdf`.

Key audit findings (file:line refs):

| # | Finding | Where |
|---|---------|-------|
| 1 | No multi-panel planning; skill mandates "exactly ONE route" | `create-scientific-figure/SKILL.md:110` |
| 2 | `_NO_TEXT` strips "NO arrows, NO callouts" from every artwork | `generate_artwork_tool.py:28` |
| 3 | SVG engine already has curved/elbow routes, tapered arrows, 5 marker kinds (`triangle/soft/bar/circle/diamond`), gradients, semantic pathway styles — **never exercised**: `read_labels` only emits `annotation` (leader+dot) | `figures_overlay.py:256-347`, `read_labels_tool.py:227-238` |
| 4 | Oracle alignment (documented "#1 failure") is never verified | `SKILL.md:132-134`, no check in `read_labels_tool.py` |
| 5 | Snap-to-ink grabs nearest ANY ink in a 12% window — wrong-structure captures on dense figures | `read_labels_tool.py:96-125` |
| 6 | No fallback when the oracle draws no leader lines (model guesses) | `read_labels_tool.py:37-39` |
| 7 | No label de-collision (`arrange_labels` was removed) | `figures_plugin.py:9-11` |
| 8 | No edit tools, no lineage manifest — versioning by filename (`*_final_v4.svg`) | figures plugin registers 5 tools, none edit |
| 9 | `verify_figure` + white-bg invariant are optional prose, no code gate | `SKILL.md:203-211`, `generate_artwork_tool.py:205` |
| 10 | Style direction duplicated (`.toml` templates vs in-code `_STYLES`) — drift risk | `generate_artwork_tool.py:49-68` |
| 11 | PPTX export collapses all arrowheads to `triangle`, skips `raw` elements silently | `export_pptx_tool.py:134-142,303` |
| 12 | 1K resolution cap (KEEP for now, per user) | `agent.toml:28` |

What is genuinely good and must be kept: the deterministic SVG engine (`figures_overlay.py`), the
strip-labels edit (trusted), the layered SVG + real-PPTX export story (already beats BioRender AI's
flat PNGs), the template gallery, the grounding/reference loop, `verify_figure`, and Playwright
rendering.

---

## 4. Gap analysis — each observed failure → root cause → fix

| Observed failure (user) | Root cause | Fix (§5 ref) |
|---|---|---|
| Only 1 panel produced when request implies 2 (cross-section + fuel flow) | Skill forbids it (ONE route); no decomposition step | Figure Spec planning + let the model compose multi-panel (5.2) |
| Final SVG has no arrows, only lines/dots | Pipeline only reads label/leader pairs; flow arrows never captured; only `annotation` emitted | Annotation-layer extraction preserves NB's arrows; semantic re-emit through existing arrow engine (5.1) |
| Never curved/styled arrows despite engine support | Nothing feeds `arrow` elements with routes/heads/styles | Same as above + skill teaches pathway grammar (5.1, 5.5) |
| Vector labels badly misplaced | VLM coordinate readback noise + unverified oracle drift + snap-to-any-ink | Positions come from pixels (OCR boxes + traced strokes), not VLM coordinates (5.1) |
| Raw NB image sometimes has no leader lines; SVG step can't catch it | No detection, no fallback | Leader-presence check in the extraction step; fallback to oracle route or "add leaders" edit pass (5.3) |
| "Fix the nozzle" → agent doesn't know what to edit, output regressions | No edit router, no manifest, no region redraw | Manifest + edit router + `region_redraw` tool (5.4) |
| Accuracy of content below FigureLabs | Verification optional; grounding optional; (also: check our image-model currency vs NB 2) | Verification gates + mandatory grounding trigger (5.6) |

---

## 5. The redesign

### 5.1 New default route: labeled PNG first; EXTRACT to SVG only on demand

**Vectorization is lazy** (user decision 2026-07-09): the default deliverable is the labeled
Nano Banana PNG. The SVG/editable layer is produced only when the user asks to convert to
editable OR clicks an **Edit-as-SVG** button in the client. This mirrors FigureLabs' own
economics (generation 50cr; vectorize 150cr as a separate user-triggered step), makes the first
response faster/cheaper, and lets the user iterate on the raster until happy before vectorizing.

**Phase 1 — generate (every request):**

1. Figure Spec (§5.2) → **generate the labeled figure `L` directly** — `allow_text:true`,
   prompt asks for the full figure *with* labels, leader lines, and flow arrows in house style
   (this is where NB shines; the user confirms raw labeled NB output is high quality with
   correct labels).
2. Gates on `L` (white-bg check + `verify_figure` vs spec, §5.3/5.6) → deliver the **PNG** and
   write the **manifest stub** (request, spec, template, path to `L`, state=`raster`).

**Phase 2 — vectorize (only on user request / Edit button):**

3. **Strip → `T'`** (existing, trusted). Because `T'` is derived *from* `L`, pixel alignment is
   guaranteed — the oracle-drift class of bugs disappears structurally.
4. **Diff `L` vs `T'`** → binary annotation mask = exactly what NB drew as annotations (text
   glyphs, leaders, arrows), in exact position. Also a free alignment check: if the diff
   covers a large area fraction, the strip drifted → retry strip.
5. **OCR the text regions** (in-mask, white-margin, clean sans-serif — the easy OCR case) →
   emit real `<text>`/`label` elements at the OCR box positions. Erase text boxes from the mask
   (white-fill; labels sit on margins).
6. **Vectorize the remaining strokes** (arrows + leaders), two tiers:
   - **Tier 1 (semantic, default):** skeletonize each connected stroke → fit Bézier centerline →
     detect arrowhead blob at endpoints (contour area/shape at stroke tips) → sample stroke color
     from `L` → re-emit as `arrow`/`leader` elements through `figures_overlay.py` with the right
     route (`curved`/`elbow`), head kind, width, and color. Truly editable objects, NB's placement.
   - **Tier 2 (visual fallback):** binary-mode trace (vtracer/potrace) of the stroke mask →
     `raw` paths that look identical to NB's marks. Used when centerline fitting fails QA.
7. **Compose** (existing `compose_figure_layers`): `T'` as raster `<image>` + vector text +
   semantic arrows. Same deliverable format as today, but the annotations are NB-placed.
   Manifest state → `vectorized`.

**WYSIWYG guarantee:** the PNG the user approved is exactly what gets vectorized — extraction
preserves NB's marks instead of re-synthesizing them, so "convert to editable" cannot change how
the figure looks (beyond font substitution on the OCR'd text).

**Trigger plumbing:** agent-side, "make it editable" routes the skill to Phase 2 via the
figure's manifest (most recent figure by default). Client-side, the Edit-as-SVG button on an
image artifact card can simply send the canned convert request referencing the figure (the
artifacts channel already knows which file it renders); a dedicated RPC can come later.

New tool: `extract_annotations(labeled, base) -> elements[]` (one plugin tool wrapping steps 3-5;
pure deterministic pixel work). The existing oracle route (`read_labels_from_image`) **stays** as
fallback Route B.

### 5.2 Figure Spec planning step (multi-panel)

Add a mandatory lightweight planning step to the skill (LLM, no new tool): for any non-trivial
request, write a **Figure Spec** — panels (with A/B letters), entities per panel, labels at the
right depth, flows/arrows with semantics (flow/activation/inhibition…), layout, template. Then:

- **Default:** generate the WHOLE multi-panel figure in one `generate_artwork` call (drop the
  single-subject framing; NB composes panels natively — this is exactly what FigureLabs relies
  on; confirmed reliable when the prompt states the layout explicitly, see §6).
- **Complex/large specs:** generate panels separately and add a small `compose_panels` tool
  (grid/row layout, panel letters as vector text, shared white background) — the compositor we
  currently lack.

Delete the "pick exactly ONE route" rule; replace with "pick ONE route *per panel*".

### 5.2b Strip-independence (added 2026-07-10 after a real failure)

First live "make it editable" run failed: the strip (Nano Banana) **under-removed** — it left most
labels in the base and slightly redrew the artwork, so the pixel-diff saw almost nothing (diff
1.2%) and the diff-only OCR caught just the 2 titles; the `read_labels_from_image` fallback then
hard-crashed on a truncated VLM JSON array. Fixes, so extraction degrades gracefully instead of
returning nothing:

- **OCR the LABELLED image directly**, not the diff mask — every label is read regardless of strip
  quality. This is the core fix (text no longer depends on the strip working).
- **Clean base by whitening the labels the strip LEFT** (per-box: if the base still matches L there,
  whiten it) — prevents baked-text-under-editable-text doubling when the strip under-removes.
- **Filter strokes to real annotations**: keep a traced stroke only if it has an arrowhead OR an
  endpoint near a label box; drop artwork-redraw noise (killed the "46 bogus leaders").
- **Salvage truncated VLM JSON** in `vision_gemini.parse_json` (recover the complete `{...}` objects)
  so the fallback survives an over-long label list.
- Graceful degradation contract: **labels always become editable** (OCR-on-L); leaders/arrows become
  editable when the strip works, else stay as baked raster in the (text-cleaned) base.

### 5.3 Robustness checks in the extraction step (deterministic, cheap)

- **Alignment gate:** diff-area fraction threshold → retry strip.
- **Leader-presence gate:** every OCR'd label should have a stroke within r px of its box; labels
  without anchors → run one "add leader lines" edit pass on `L`, or fall back to Route B for
  those labels.
- **White-bg / no-shadow gate:** numpy border + corner sampling on `T'` (the `_FINISH` invariant,
  finally measured) → retry artwork if violated.
- **Label de-collision:** simple greedy vertical nudging of margin label boxes (port of the
  adjustText idea, trivially small since NB already spreads them).

### 5.4 Edit system (the "fix the nozzle" story)

- **Figure manifest** (JSON sidecar per figure): request, spec, template, paths to `L`, `T'`,
  mask, elements JSON, overlay SVG, final SVG/PNG, version chain. Written by compose; read by the
  edit router. Kills the `*_final_v4.svg` filename archaeology.
- **Edit router** (skill logic, explicit decision table). The router is **state-aware** via the
  manifest: on a `raster`-state figure (not yet vectorized) fixes are NB raster edits of `L`
  directly (region-redraw / text-edit on the image, FigureLabs-style); the layer-based paths
  below apply once state=`vectorized`. For label-text fixes on a raster figure the router may
  suggest vectorizing first — element edits are cheaper and drift-free.
  - *Artwork content* ("fix the nozzle shape") → new tool `edit_artwork_region`: NB edit of `T'`
    with the instruction + optional region hint → re-strip → re-extract only affected annotations
    → re-compose. (= FigureLabs Region Redraw.)
  - *Label text* ("rename X to Y") → mutate the element in the manifest's elements JSON →
    re-render overlay + compose. No image model call. (= their Text Edit, but cheaper — ours is
    already vector.)
  - *Annotation geometry/style* ("make the arrows blue/curved") → element mutation, re-render.
  - *Style/global* ("make it watercolor") → regenerate with new template, reuse spec.
  - Router always states which layer it's editing and returns the same manifest, bumped version.

### 5.5 Exercise the arrow engine from the skill

Even before extraction lands: the skill should instruct emitting `arrow` elements with the
semantic pathway styles (`flow/activation/inhibition/transport…`) whenever the spec contains
flows — today the vocabulary exists (`figures_overlay.py:332-347`) and is simply never used.
Default leaders should get `route:"curved"` + `soft` heads for the BioRender look where
appropriate, instead of always elbow+dot.

### 5.6 Verification as a gate, not a suggestion

- `compose_figure_layers` (or the skill contract) requires a `verify_figure` pass + the
  deterministic gates of 5.3 before a figure may be declared final; failed gates trigger ONE
  automatic repair cycle (regenerate the failing layer), then surface honestly.
- Keep the grounding loop; tighten its trigger wording ("if a domain expert could name a part you
  can't, research first").
- Check model currency in config (`gemini-3-pro-image`) vs FigureLabs' NB 2/Pro tier — cheap win
  if we're a model generation behind. (Also worth adding SeeDream/Flux via existing provider
  routing as user-selectable alternates — we already have the plumbing.)

### 5.7 Explicitly out of scope for now (per user / by judgment)

- 1K→2K resolution bump (knob exists; revisit after quality work).
- Full-image vectorization as default (blob-soup risk; stays opt-in via `trace_image`).
- Built-in vector canvas UI (desktop client work; SVG-in-Illustrator/PPT covers it).
- StarVector/OmniSVG as pipeline stages: StarVector (CVPR 2025) is the only verified
  semantic-primitive vectorizer (emits rectangles/arrows/text where VTracer emits blobs), but its
  encoder tops out at 224–384px input — it cannot ingest our figures (details in §6). Recraft's
  vectorize API is confirmed non-semantic (outlined glyphs). Track, don't adopt.

---

## 6. Implementation stack (validated 2026-07-09)

**New pip dependencies — 3 (maybe 4) packages, all Windows-wheeled, no torch/paddle/GPU:**

| Package | Version | License | Role |
|---|---|---|---|
| `rapidocr` | 3.9.1 | Apache-2.0 | OCR text + quad boxes — same PP-OCRv5 models as PaddleOCR but via onnxruntime, no paddle framework |
| `vtracer` | 0.6.15 | MIT | Fallback vectorizer for filled blobs (arrowheads, unOCRable glyphs); `colormode='binary'`, `mode='spline'`, `filter_speckle≈8` |
| `svgpathtools` | 1.7.2 | MIT | Path assembly / Bézier math |
| `scikit-image` | (if not present) | BSD-3 | `skeletonize` + `medial_axis` (centerline + stroke width recovery) |

**Vendored/ported (no dependency):**
- **Schneider Bézier fitting:** no maintained PyPI package exists — vendor
  [volkerp/fitCurves](https://github.com/volkerp/fitCurves) (MIT, two numpy-only files,
  `fitCurve(points, maxError)` → cubic segments). Fit a straight line first (max deviation < 1px
  → it's a line); Bézier only for genuinely curved strokes.
- **Skeleton→graph walker:** skip sknw/skan (both drag in numba) — a ~40-line BFS walker over
  skeleton pixels is adequate at annotation density (dozens of strokes per figure).
- **Label de-collision:** port adjustText 1.4.0's bbox-repel loop (~100 lines, MIT) rather than
  depending on matplotlib; constrain movement along the margin axis, re-anchor leader tails.

**Zero-new-dep OCR alternative:** Gemini itself (existing dep) — but its bounding boxes are
verifiably imprecise, so use it only to *read* text per diff-mask component and **snap its
0–1000-normalized boxes to the mask's exact component geometry** (the mask already knows *where*
everything is; the VLM only supplies *what it says*). RapidOCR is the deterministic/offline default.

**Key technical facts that shaped the design:**
- vtracer and potrace are **fill tracers** — a 2px leader becomes a filled ribbon (double outline),
  never a stroked path. Centerline tracing must go through skeletonization; there is no shortcut
  library. (pypotrace doesn't build on Windows; `potracer` works but is GPLv2 — license risk.)
- **Arrowhead detection has no established library** — bespoke and cheap: the `medial_axis`
  distance transform gives stroke half-width along the skeleton; an arrowhead is a monotonic
  widening over the last ~5–15px of one endpoint. Confirm with the residue-blob triangle test
  (`cv2.approxPolyDP` ≈ 3 vertices → tip = farthest vertex → head orientation).
- **Text removal is mostly unnecessary**: `T'` already *is* the clean base; "removing text" is
  mask arithmetic (subtract OCR quads from the diff mask), not raster inpainting.
  `cv2.inpaint(..., INPAINT_TELEA)` only for the rare label overlapping artwork.
- **Nano Banana Pro multi-panel is confirmed reliable** from a single prompt (Google's own
  prompting guide demonstrates multi-panel infographics; practitioners confirm) when the panel
  layout is stated explicitly ("two panels side by side: left = labeled cross-section…; right =
  4-step process flow"). Caveat: small-text spelling slips happen → the verify gate stays.
  ⚠ **1K flag:** Google's guidance says 2K+ makes label text crisp; at our 1K cap, OCR misreads
  are the one place the resolution cap may bite. Keep 1K per user direction, but if OCR accuracy
  disappoints, the first knob to try is 2K.
- **StarVector confirmed non-viable** for us: image encoder is 224px (1B) / 384px (8B), 8k/16k
  token ceiling, GPU-bound, fails on illustrations per its own repo — cannot faithfully ingest a
  1024px labeled figure. **OmniSVG**: weights exist (4B/8B) but scope is icons/anime,
  re-imagines rather than traces. **Recraft**: vectorize API ($0.01/img) is vtracer-class output
  — outlined glyphs, no semantic arrows, no live `<text>`. None replaces the deterministic
  extraction pipeline today; the diff-mask route gets exact geometry for free.

---

## 7. Phased build plan

*Status 2026-07-09: **P1–P4 BUILT** (uncommitted). New: `plugins/vectorize/vectorize_extract.py` +
`extract_annotations_tool.py`, `plugins/figure-art/edit_artwork_tool.py`, white-bg canvas check in
`generate_artwork`, `elements_path` on `render_editable_overlay`/`export_pptx`, both skills
rewritten (Figure Spec, multi-panel, lazy vectorize, manifest convention, edit router),
`figures-vector` extra in pyproject/requirements. Tests: `tests/test_extract_annotations.py`
(6 passing, incl. end-to-end with real OCR). svgpathtools dropped — waypoints + the engine's
Catmull-Rom `curved` route replaced raw Bézier emission. Manifest is a skill convention (JSON
sidecar), not a tool. Daemon restart needed to pick up the new tools.*

| Phase | Scope | Payoff |
|---|---|---|
| **P1** ✅ | Figure Spec planning step + drop ONE-route rule + skill emits semantic `arrow` elements | Multi-panel parity + styled arrows appear immediately, zero new code |
| **P2** ✅ | `extract_annotations` tool (diff→OCR→trace→semantic re-emit) + on-demand vectorize flow with manifest state machine | Label placement accuracy + NB arrow fidelity — the core gap |
| **P3** ✅ | Manifest + edit router + `edit_artwork` | "Fix the nozzle" works; FigureLabs edit-op parity |
| **P4** ✅ | Deterministic gates (alignment, leaders, white-bg) + mandatory verify + repair cycle | Consistency; kills the "sometimes it just ships broken" tail |
| **P5** | `compose_panels` tool, model currency/alternates, de-collision, PPTX arrowhead fidelity, desktop Edit-as-SVG button on image artifacts | Polish + export parity |

---

## 8. Open questions

1. **FigureLabs vectorizer teardown** — export one SVG from their free tier and inspect: live
   `<text>` or outlined paths? semantic arrow objects or contour soup? layered groups? This is
   the single most valuable remaining datapoint and needs a manual account/export (user action).
2. Whether their PPTX export is real shapes or embedded pictures (same teardown).
3. Our image-model currency (config says `gemini-3-pro-image`; FigureLabs ships "Nano Banana
   2/Pro") — verify what's actually available to us.
4. Figure Refiner (upscale/denoise on uploads) — worth matching later via a cheap upscaler?

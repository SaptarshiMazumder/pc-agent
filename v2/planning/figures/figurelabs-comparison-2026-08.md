# Figure Creator vs FigureLabs — verified capability teardown

*20 August 2026. Their site rendered live in a headless browser (home, pricing, about, api,
api/pricing, plot, flowchart, help-center — their help centre carries a complete feature
inventory). Our side read from the repository at HEAD, branch `feature/admin`. Supersedes the
9–10 Jul 2026 report in `figurelabs-parity-plan.md`, which is now materially out of date.*

**Scorecard: we lead 7 · parity 9 · they lead 18 · built-but-dead-in-production 3.**

On what a figure *is* — layered, editable, verified — we are still ahead. On everything around
it — products, models, resolution, canvas, library, API, price, distribution — they have pulled
further ahead than the July report recorded. And the one capability we built specifically to beat
them is not installed in either runtime we ship.

---

## 0. Evidence basis

Every claim below is one of three things and is marked as such:

- **Site-verified** — read off figurelabs.ai on 20 Aug 2026 via a rendered browser session.
- **Code-verified** — read from this repository at HEAD: tool registrations, plugin sources, the
  agent's skills, the Dockerfile, the desktop runtime build, git history.
- **Unverified** — stated as unknown, never counted in the scorecard.

Two things I could not check and will not guess at:

1. **What their SVG and PPTX exports actually contain today.** That needs a paid account and a real
   export. Our teardown of their vectorizer (whole-image blob trace + editable text redrawn on top,
   ghost artifacts where text sat on colour) is from 10 Jul 2026 and they have shipped a lot since.
2. **Our own hosted generation end-to-end.** The account has zero credits, so no figure has been
   produced on AWS.

---

## 1. What FigureLabs is today (site-verified)

The July report described a figure generator with a vectorizer bolted on. That is out of date. They
now run **three products and a developer API** on one credit balance, and they have restyled from
"The BioRender Companion" to "The World's First AI Agent for Scientific Illustration" — the same
word we use for what we build.

| Product | What it does | Exports |
|---|---|---|
| **AI Scientific Illustrator** | Text / document (PDF, Word, TXT), sketch, reference image, or a damaged figure → publication figure. 9 selectable image models, 6 style presets, 20+ colour palettes, palette extraction from a reference image, and "Visual Consistency" — upload a figure to lock its icons, arrows and fonts across a whole paper. | PNG, JPG, PDF, PPTX, SVG |
| **AI Flowchart Maker** | Text, sketch, reference or template → flowchart. AI auto-layout; template library (flowcharts, model architectures, cycle diagrams, timelines); full node editing in canvas — drag nodes, adjust connectors, multi-select, retype text, one-click colour modes. | PNG, JPG, PPTX, SVG, source file |
| **AI Plot Maker** — *new since July* | CSV / Excel / pasted data → journal-styled plot. Chart type, journal style, palette; refine by chat; **panel assembly** — auto-align and label finished figures into a multi-panel layout. | PNG, JPG, SVG, PPT, **and the Python source** |
| **Public API** — *new since July* | One async endpoint routing text/sketch/reference/enhance/recolour/ratio automatically, plus flowchart, upscale, vectorize. Wallet top-ups, 7-day file storage tiers, enterprise contracts. Pitched at "research platforms, AI agents, ELNs, LIMS". | $0.80/figure, $0.50/vectorize, $0.02–0.08 upscale. Failed and rejected tasks not billed. |

**Editing layer.** Region Redraw, Text Edit (click text in the image and retype), Recolor, White-BG
removal, Aspect Ratio, Upscale 2K/4K/8K — each a discrete billed operation. Then an **infinite
canvas**: upload external images, annotate, add text, shapes, lines, pencil marks, organise with
frames and auto-layout. After vectorizing, a **vector canvas** with real element properties — fill
by hex, stroke colour/width/dash, opacity.

**Account layer.** Projects (every past session with its conversation history), a Library with
folders, a vector-canvas workspace, password-protected share links (recipient watches the
conversation, cannot edit), Team workspaces with pooled credits, and a downloadable **Publication
Authorization certificate** — a PDF with a unique ID asserting the right to publish commercially.
The free tier is explicitly non-commercial; that certificate is what they sell to a nervous
corresponding author.

### Their price of one figure

| Operation | Credits | ≈ USD on Plus annual | API price |
|---|---|---|---|
| Generate 1K · Region Redraw · Regenerate · White BG | 50 | $0.20 | $0.80 |
| Text Edit | 60 | $0.24 | $0.80 |
| Upscale 2K / 4K / 8K | 10 / 20 / 40 | $0.04 / $0.08 / $0.16 | $0.02 / $0.04 / $0.08 |
| Vector export — SVG or PPTX | 150 | $0.60 | $0.50 |

Plans: Free $0 (150 one-time + 50 daily credits, 1K max, no SVG/PPTX, no canvas, Nano Banana Pro
only, non-commercial) · Starter $12/$10 (1,000/mo, 4K) · Plus $35/$20 (5,000/mo, 8K, all models) ·
Pro $99/$54 (20,000/mo) · Team $35/$20 per seat, pooled, 20% rollover · Top-ups $15–$90.
The USD column is my arithmetic on the Plus annual rate (1 credit ≈ $0.004), not their figure.

---

## 2. What we ship (code-verified)

Figure Creator is an agent, not a form: 16 single-purpose tools across five plugins, plus the whole
rest of the daemon's toolbox (web research, document reading, PlantUML, shell, browser) because
`agent.toml` deliberately carries no tool allowlist. Its style gallery is 9 art templates as data
files. The default pipeline is the native-label route:

```
generate_artwork(template, subject, allow_text:false)      → textless base T
generate_artwork(reference_images=[T], allow_text:true)    → labelled oracle L
read_labels_from_image(image=L, base=T)                    → label text + leader endpoints, snapped to ink
render_editable_overlay(elements)                          → live <text> + arrows, transparent
compose_figure_layers(artwork=T, overlay)                  → layered SVG + flattened PNG
verify_figure(image, expected)                             → {ok, missing, extra, wrong}
export_pptx / export_pdf                                   → real shapes / vector PDF
```

Beyond that route we hold tools they have no equivalent of: `figure_to_svg` (one-call, stateless
raster→layered-SVG with *semantic* arrow reconstruction), `extract_annotations`, `edit_artwork`
(region-scoped raster edit), `layout_flowchart`, `trace_image`, `find_reference_image`, and a
measured white-background gate on every generation. The app window carries a fabric.js canvas —
pen, marker, rect, ellipse, arrow, text, crop, undo/redo, zoom, save-as — and uniquely,
**send the annotated image back into the chat**.

---

## 3. Three findings (code-verified)

### Finding 1 — the moat is not installed anywhere we ship

`figure_to_svg`, `extract_annotations` and `trace_image` need the `figures-vector` extra (numpy,
scikit-image, rapidocr, onnxruntime, vtracer). The hosted daemon image installs
`project.dependencies` plus the `mcp` extra and nothing else. The desktop installer builds its
embedded runtime from `"${Wheel}[mcp]"`. Neither includes the extra. The dev venv does — which is
exactly why this has never shown up in testing.

So on AWS today, and in the .exe, the **Convert to Vector** button on an image artifact returns an
install error, and the tier built specifically to beat their vectorizer does not exist for any real
user. The default generate→label→compose route is unaffected — it imports neither numpy nor
skimage, so it works hosted.

```
deploy/docker/Dockerfile        deps = p["dependencies"] + optional["mcp"]   ← no figures-vector
clients/desktop/scripts/build-runtime.ps1
                                pip install "${Wheel}[mcp]"                  ← no figures-vector
plugins/vectorize/*             numpy · skimage · rapidocr · vtracer         ← all in that extra
plugins/{vision,figures,figexport,figure-art}
                                no numpy / skimage imports                   ← default route survives
```

### Finding 2 — a file move silently reverted the playbook

The P1–P4.5 rewrite of `create-scientific-figure/SKILL.md` — Figure Spec planning, multi-panel
decomposition, lazy vectorization, the figure manifest, the edit router — existed at commit
`1e70f26`. Commit `e479b58` ("backup commit, for agent exe and UI code to live entirely inside
agent/<id>") rewrote both skill files with older copies: 433 lines changed, 272 deleted. HEAD is
the old version.

```
occurrences in create-scientific-figure/SKILL.md
                        1e70f26   e479b58   HEAD
figure_to_svg               2         0       0
extract_annotations         6         0       0
manifest                    8         0       0
multi-panel                 3         0       0
Figure Spec                 3         1       1
```

The result: an agent whose *tools* know about semantic extraction and whose *instructions* do not.
Step 2A still reads "the ONE route for labelled figures"; neither skill mentions `figure_to_svg` or
`extract_annotations` at all. Tool descriptions are self-advertising so the model may still find
them — but the playbook routes it elsewhere, and multi-panel figures, which FigureLabs gets free
from the raster model, are still discouraged by our own skill.

### Finding 3 — four model calls per figure, capped at 1K, no upscale

Our default route spends two image generations (textless base, then the labelled oracle) plus two
VLM calls (read labels, verify) per figure. Theirs spends one. That is roughly double their unit
cost and several times their latency — they claim ~30 seconds.

On top of that, `agent.toml` pins `resolution = "1K"` for cheap iteration, overriding every
template's own 2K. The tool supports 1K/2K/4K and we have no upscaler at all, so our ceiling is 4K
native against their 8K ≈ 1200 DPI — and 8K is the number a journal submission system asks for. The
cap is one line; the missing upscaler is not.

The compensation is real: their one call bakes labels into pixels, ours produces live vector text
over clean artwork. We pay two calls to get an editable deliverable they charge 150 credits to
approximate afterwards.

---

## 4. Capability matrix

Verdict key: **Ours** = we are genuinely better, not merely present · **Dead in prod** = built, no
user can reach it.

### Getting to a first figure

| Capability | FigureLabs | Figure Creator | Verdict |
|---|---|---|---|
| Text → figure | Yes, 9 models | Yes, Gemini 3 Pro Image, pinned | Parity |
| Document → figure | PDF, Word, TXT | PDF, docx, xlsx, pptx (base deps) + web research to ground it | **Ours** |
| Sketch / photo → figure | Yes | Yes — `conditioning:"sketch"`; fal/replicate give real ControlNet | Parity |
| Reference → figure | Style and layout match | `conditioning: style \| layout \| sketch` | Parity |
| Model choice | 9 models, user-switchable, tier-gated | One, config-pinned. Proxy routes wildcard so plumbing exists; no picker | **Theirs** |
| Style presets | 6 named (Flat, 2.5D, 3D, Sketch, Line-Art, Hand-Drawn) | 9 art templates as editable data files w/ palette, aspect, model, exemplars | **Ours** |
| Colour palettes | 20+ presets + extraction from a reference image | `palette` hex list per call; no library, no extraction | **Theirs** |
| Cross-figure consistency | Upload one figure to lock icons/arrows/fonts for a paper | Palette passable by hand; nothing enforces it | **Theirs** |
| Multi-panel composition | Native from the raster model; Panel Assembly auto-arranges + labels | Skill still says pick one route; `compose_panels` planned, never built | **Theirs** |
| Speed to first figure | ~30s claimed, one call | Four model calls plus agent turns | **Theirs** |

### Fixing it

| Capability | FigureLabs | Figure Creator | Verdict |
|---|---|---|---|
| Region redraw | 50 credits | `edit_artwork` with an optional pixel box | Parity |
| Fixing label text | Text Edit, 60 credits, AI rewrites pixels | Text is already live `<text>` — retype it, free, no model call, no drift | **Ours** |
| Recolour | One-click, palette or reference | Only by regenerating with a new palette | **Theirs** |
| Background removal | One-click White BG, 50 credits | Inside `figure_to_svg` only; white bg is a measured invariant on every generation instead | Parity |
| Aspect ratio change | Post-hoc, one click | At generation only | **Theirs** |
| Upscale | 2K / 4K / 8K | None. 4K native ceiling, pinned to 1K | **Theirs** |
| Enhance / repair an old figure | Repair, upscale, redesign | Redraw yes, repair-and-upscale no | **Theirs** |
| Correctness check | None advertised | `verify_figure` vs expected structures; skill makes it mandatory pre-export | **Ours** |
| Grounding before drawing | None — model priors are the ceiling | Mandatory research step, `find_reference_image`, sketch/reference conditioning | **Ours** |

### The editable deliverable

| Capability | FigureLabs | Figure Creator | Verdict |
|---|---|---|---|
| Layered SVG with live text | 150 credits; whole-image trace + text redrawn on top (teardown 10 Jul, may have improved) | Native: raster artwork as `<image>`, labels as real `<text>`, never traced | **Ours** |
| Semantic arrows in the SVG | Traced blobs, per our teardown | Real arrow objects — pixel centreline, detected arrowheads, curved/elbow routes, 5 marker kinds | **Dead in prod** |
| Any raster figure → editable SVG | Upload-and-vectorize, 150 credits | `figure_to_svg`, one stateless call + Convert-to-Vector button on any PNG | **Dead in prod** |
| Whole-artwork tracing | Default behaviour | `trace_image`, deliberately opt-in | **Dead in prod** |
| Editable PPTX | Yes, 150 credits. Real shapes or embedded picture: **unverified** | Every element native: text boxes, pill labels, connectors with real arrowheads, elbow chains, node boxes w/ embedded icons, separate movable pictures | **Ours** |
| Vector PDF | PDF export | Vector PDF, selectable text, headless Chromium | Parity |
| Raster export ceiling | 8K ≈ 1200 DPI | 4K native, 1K as configured; PNG only, no JPG | **Theirs** |

### Canvas and workspace

| Capability | FigureLabs | Figure Creator | Verdict |
|---|---|---|---|
| Annotate an image | Infinite canvas — text, shapes, lines, pencil, frames, auto-layout, external uploads | fabric.js per artifact: select, pen, marker, rect, ellipse, arrow, text, crop, undo/redo, zoom | **Theirs** |
| Annotation → back into the work | Not offered | Mark up the figure, send the marked-up PNG into chat as the next instruction | **Ours** |
| Vector element properties | Fill hex, stroke colour/width/dash, opacity | Move, scale, rotate, retype text, 6-colour palette, stroke width | **Theirs** |
| Multi-figure board | Infinite canvas with frames | One artifact at a time | **Theirs** |
| Past work | Projects with full history, Library with folders | Chats, projects, recents, workspace file tree | Parity |

### Adjacent products

| Capability | FigureLabs | Figure Creator | Verdict |
|---|---|---|---|
| Flowcharts | Templates, AI auto-layout, full node editing in canvas | `layout_flowchart` + illustrated icon nodes, plus PlantUML for sequence/class/ER/state/activity/mindmap/gantt — far more diagram types | Parity |
| Data → plots | Whole product: CSV/Excel, journal styles, chat refinement, Python source export | Nothing. No plotting tool, no matplotlib anywhere in the plugins | **Theirs** |
| Developer API | Public, async, wallet-billed, per-capability endpoints | None productized | **Theirs** |

### Account, trust, commerce

| Capability | FigureLabs | Figure Creator | Verdict |
|---|---|---|---|
| Sign-in and balance | Credits, daily refresh, referrals, top-ups | Platform account, credits, buy-credits in Settings — same identity as the agentd shell | Parity |
| Publication rights certificate | Downloadable PDF w/ unique ID; free tier non-commercial | Nothing. We have signing infrastructure and no product built on it | **Theirs** |
| Sharing a figure | Password-protected view links | None | **Theirs** |
| Teams | Team workspace, shared assets, pooled credits | Org tenancy + admin panel at platform level; nothing figure-specific | **Theirs** |
| Data handling | "We never train on your uploads" | Runs on your machine or your keys; desktop build needs no cloud at all | **Ours** |
| Distribution | 600K+ researchers claimed, journal-credit gallery, affiliates, blog, tutorials | One agent on a platform with no users yet | **Theirs** |

---

## 5. What each side is actually selling

**Our real advantage**

- **The figure is born editable.** Their SVG is reconstructed after the fact and charged for; ours
  is what the pipeline produces. Live `<text>` and real arrow objects, not outlined glyphs and
  traced blobs.
- **Correctness is engineered.** Mandatory grounding, a VLM verify gate, a measured white-background
  invariant. They ship whatever the model drew.
- **It is an agent, not a form.** It can read the paper, search for a reference micrograph, ask a
  clarifying question, then draw — with the whole daemon toolbox behind it.
- **The templates are data.** Adding a house style is a file, not a release.
- **Ownership.** Local or BYOK, no lock-in, no non-commercial free tier.

**Their real advantage**

- **Coverage of the researcher's whole day.** Illustration, flowcharts, plots, API — one balance,
  one login. Plots alone are a product we do not have.
- **Choice.** Nine models, six styles, twenty palettes, three upscale tiers.
- **The last mile.** 8K ≈ 1200 DPI, a publication-rights certificate, share links, team credits —
  the things that get a figure through a submission system and past a co-author.
- **Iteration speed.** One call, ~30 seconds, every fix a discrete cheap operation on the image.
- **Distribution.** They are in front of researchers. We are not.

The uncomfortable summary: we win the argument about what a scientific figure *should* be, and they
win nearly every argument about what a product needs to do. Our differentiators are real and hard
to copy — and two of the three are currently unreachable by any user.

---

## 6. Fix order

1. **Add `figures-vector` to both runtimes.** One line in the Dockerfile's dependency resolution,
   one in `build-runtime.ps1`. Highest-value change in this document: it turns three
   shipped-but-dead tools live, including the one capability we have that they demonstrably lack.
   Cost is image size — onnxruntime and the OCR models are not small.
2. **Restore the clobbered skills from `1e70f26`** and re-merge onto HEAD by hand (the newer file
   has other edits). Gets back multi-panel, the manifest, the edit router, lazy vectorization —
   features already paid for and not in use.
3. **Unpin the resolution and add an upscale tool.** 1K is why label OCR is fragile and why we
   cannot answer "does it meet the DPI requirement" with a yes.
4. **Expose model choice.** The proxy already routes wildcard; this is a settings control and a
   per-call parameter, not an engine change. One of their loudest selling points, one of our
   cheapest to match.
5. **Then pick a lane on breadth.** Plots are a genuinely separate product. A palette library and a
   consistency lock are an afternoon each and close two visible gaps. The publication certificate is
   small, and is the one trust artifact a corresponding author actually asks for.

**Not recommended yet:** chasing their infinite canvas, or an API product. Both are large, and
neither is what stops someone choosing us today.

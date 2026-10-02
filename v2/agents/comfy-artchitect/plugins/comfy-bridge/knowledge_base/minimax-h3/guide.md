# MiniMax H3 — the agent's guide

> **LICENSE WARNING.** The open weights are under the *MiniMax H3 Community License*: **not licensed in the
> USA, EU, UK or Korea** (outputs may not be used there either); products over **$20M yearly revenue** need
> MiniMax's written authorization; commercial UIs must display **"Powered by MiniMax H3"**; and **commercial
> use of locally generated outputs requires MiniMax's commercial license, sold only through Comfy** (Comfy
> Cloud generations include it). Say this to the user before the first H3 render. The weights themselves are
> free to download; `cost` is `free`.

Open-weights omni-modal video model (MiniMax, open since 2026-08-03): one pass produces 24 fps video **with
native stereo audio** — dialogue, SFX and music together. Two checkpoints: **FL2VA** (text, first frame,
last frame, both) and **Ref2VA** (up to 9 images / 3 videos / 3 audio clips as references, plus keyframe
guides anywhere on the timeline, plus Fun ControlNet). 4–15 s per clip, 768p native. Everything below was
read from the official templates, node source and docs on 2026-10-01; `profile.json` holds the files,
numbers and sources.

## When to pick it, and which recipe

- **Pick H3** when the clip needs **sound** (speech in 11 languages, lip-sync, SFX, score), **on-screen
  text that must read cleanly**, **multi-shot timed cuts** in one clip, or **identity/voice lock from several
  references**. Wan 2.2 cannot do any of these.
- **Do not pick H3** when the user is in an excluded territory or needs commercial rights without buying the
  license; when they want 2K native (API only — generate 768p and upscale separately); when they need a
  negative prompt or CFG steering (none exists); or when the box cannot spare ~40 GB of disk.
- **Our box (60 GB disk; 32 GB RTX 5090 or 96 GB RTX PRO 6000):** every recipe uses the **pruned
  int8_convrot** checkpoint (21.0 GB) + NVFP4 encoder (15.7 GB) + two VAEs (3.4 GB) = **40.1 GB** — so only
  **one** of FL2VA / Ref2VA fits at a time (both = 61 GB). The 32 GB card holds 10 s at 864×480 (28.6 GB
  peak, community); 1344×768 × 5 s is untested but is what the docs say to render for faces. The 96 GB card
  runs the same files comfortably; the bf16 builds (40 GB) do not fit the disk next to the encoder, so more
  VRAM does not buy a bigger build here. Blackwell = cu130 int8 kernels OK.
- **Which checkpoint to download:** if the user's work is text / first-frame / first+last → **FL2VA**
  (`t2v-fl2va`, `i2v-fl2va`, `flf2v-fl2va`). If it involves references, keyframes, control video or
  continuing a clip → **Ref2VA** (`r2v-ref2va`, `multiframe-ref2va`, `fun-controlnet-*`,
  `v2v-continue-ref2va`). Ref2VA with empty reference slots is plain text-to-video (the ControlNet template
  does exactly that), so Ref2VA is the broader single download.
- **Steps:** 20 base for the final. Turbo LoRAs: **8** (fl2v) or **4** (ref2v / fl2v 4-step) for drafts —
  references and guides weaken at 4, audio is weakest below 12. FastH3 (8 fixed) is **pending**: its
  templates need ComfyUI 0.36.0, the image is 0.35.0.
- **Longer than 15 s:** chain `v2v-continue-ref2va` (last second of clip A anchored at frame 0 of clip B,
  drop the overlap when joining). Never a single latent above 362 frames.

Stage composition: a still from an image model → `i2v-fl2va` (canvas is taken from the image — the first
frame is *stretched* to the canvas); a character sheet + voice sample → `r2v-ref2va`; storyboard frames →
`multiframe-ref2va` (one identity reference, keyframes at `round(seconds × 24)`); a dance clip →
`fun-controlnet-pose-ref2va`; canny/depth frames from an earlier stage → `fun-controlnet-ref2va`.

## The recipes (one line each)

| recipe | needs | does |
|---|---|---|
| `t2v-fl2va` / `-turbo8` / `-turbo4` | prompt | 5 s clip with audio from text; 20 / 8 / 4 steps |
| `i2v-fl2va` / `-turbo8` / `-turbo4` | first frame | animates the image (geometry anchor at frame 0); canvas from the image at `megapixels` |
| `flf2v-fl2va` | first + last frame | one motion between two frames; prompt opens with the FL2VA alignment line |
| `r2v-ref2va` / `-turbo4` | 1–9 reference images (slots for videos/audio) | identity / style / motion / voice lock; six-section prompt; `ref_image_size` match or max |
| `multiframe-ref2va` / `-turbo4` | identity reference + 3 keyframes | keyframes pinned at frame 36 / 72 / 120 by chained Add Guide |
| `v2v-continue-ref2va` | a 24 fps source clip | continues it: first 22 frames + soundtrack anchored at frame 0 (official note, no official graph, not run) |
| `fun-controlnet-ref2va` | a preprocessed control clip | canny / depth / pose / HED / MLSD-driven motion, strength 1.0 |
| `fun-controlnet-pose-ref2va` / `-turbo4` | raw footage with people | SDPose multi-person skeletons extracted in-graph (+1.9 GB on disk) |
| `t2v-fasth3`, `i2v-fasth3` | pending (ComfyUI ≥ 0.36.0) | DMD2 8-step distill with sparse attention |

Not available: L2VA alone (unplug `first_frame` on `flf2v-fl2va` and use the L2VA line), inpainting (mask
route documented, no official graph), 2K.

## Prompting — the Context-IR format

H3 expects the output of MiniMax's hosted **H3-Context-IR** rewriter, which is not open. Write that
representation yourself (or have an LLM follow the two official guides). **No negative prompt exists** and a
negative line *adds* content: "no subtitles" puts subtitles in. Say what *is* in the shot ("the sign above
the door is blank"). English throughout, except dialogue/lyrics inside `<d>` tags and quoted on-screen text,
which stay in their original language. Style embeddings: `embedding:minimaxh3_art_is_explosion` (10 community
files).

### Base modes (FL2VA checkpoint: T2VA / I2VA / FL2VA / L2VA)

**Part one — instruction line** (first line, then one blank line; T2VA has none):

- I2VA: `For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.`
- FL2VA: `How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot N) aligns with the S.SS-second mark of the target video.`
- L2VA: `How the reference pictures align with the target video — <Picture 1> (from [Shot N]) aligns with the S.SS-second mark of the target video.` — N is the index of the actual final shot, S.SS the effective duration with exactly two decimals.

**Part two — three fields, in this order, separated by blank lines:**

```
integrated_multimodal_description: [Shot 1] ...

overall_soundscape: ...

non_diegetic_music: ...
```

- `integrated_multimodal_description`: visuals, actions, shots, speakers, dialogue, singing and diegetic
  audio along the timeline. `[Shot 1]` opens with style + initial composition (styles: `Cinematic`,
  `live-action`, `2D-animated`, `3D CG`, `claymation`, `watercolor`, `vintage film`), e.g.
  `[Shot 1] Live-action, cinematic, a medium-wide shot frames...`. No timestamp on Shot 1; later shots
  `[Shot 2] At 00:03.500, the camera cuts to...` with strictly increasing cut times inside the duration.
  Cut verbs: `the camera cuts to`, `the shot cuts to`, `the shot transitions to`, `the shot changes to`,
  `the shot switches to` (cross-dissolve / fade / wipe only if the user asks).
- `overall_soundscape`: 1–4 sentences of ambient, physical-action and non-verbal human sounds — no
  dialogue, singing or diegetic music here; `N/A` only for total silence.
- `non_diegetic_music`: 1–3 sentences on instrumentation, tempo, rhythm, dynamics ("do not use abstract
  mood words"); `N/A` when none.

**Camera motion = type + amplitude + speed, as prose.** Types: `Zoom In / Zoom Out`, `Push In / Pull Out`,
`Pan Left / Pan Right`, `Truck Left / Truck Right`, `Tilt Up / Tilt Down`, `Pedestal Up / Pedestal Down`,
`Arc Shot`, `Tracking Shot`, `Static Shot`, `Shake Slightly / Shake Strongly`, `POV`,
`Roll Clockwise / Roll Counterclockwise`; amplitude `with small amplitude` / `with large amplitude`; speed
`at slow speed` / `at fast speed`. Example: `The camera pushes in with small amplitude at slow speed toward the folded letter in her hands.`

**Speakers and dialogue.** Stable IDs `(S1)`, `(S2)`, compound `(S1,S2)`; identity phrase, ID, action and
delivery outside the tags, inside only `<d>[Language] exact words</d>`:
`The young woman with a quiet, breathy voice (S1) says: <d>[English] I get off at the next station.</d>`.
Voiceover: the exact phrase `says in an off-screen voiceover`, and right after the `<d>` block state that the
character's lips stay closed (community: not fully reliable — name the narrator, or supply the line as an
audio reference). Across cuts: `<scenetrans>` at both connection points + "continues seamlessly across the
cut"; truncated speech: `<cutoff>`. On-screen text in English double quotes, verbatim:
`A red neon sign reading "营业中" glows above the doorway.`

**Keyframe arcs.** I2VA: first-frame anchor → action onset → continuous development → result or reaction.
FL2VA: first-frame state → observable intermediate changes → progressively narrowing differences →
last-frame state (single shot preferred). L2VA: plausible preceding state → explicit action and transition
path → gradual convergence in the final shot → last-frame landing.

**Official T2VA example (verbatim, Case 1 of the base guide):**

```
integrated_multimodal_description: [Shot 1] Live-action, cinematic, a medium-wide shot frames a baker opening the shutters of a small street bakery before sunrise. The camera pushes in with small amplitude at slow speed as the middle-aged baker with a calm, slightly raspy voice (S1) places a fresh loaf on the wooden counter and says: <d>[English] First batch of the morning.</d> [Shot 2] At 00:05.000, the camera cuts to a close-up of steam rising from the sliced bread while the baker's final words carry over from the previous shot.

overall_soundscape: Wooden shutters scrape open over a quiet street as trays clink softly inside the bakery. The doorbell rings once, followed by light footsteps and the crisp sound of bread being sliced.

non_diegetic_music: A soft acoustic-guitar pattern at a moderate tempo, joined by sparse upright-bass notes and a gentle fade at the end.
```

The official I2VA case begins with the instruction line and then
`[Shot 1] Live-action, cinematic, the young woman shown in <Picture 1> remains beside the rain-covered train window, preserving her appearance, clothing, seat position, and the carriage layout. ...`

### Full-reference mode (Ref2VA checkpoint) — six sections, in this order

| section | purpose |
|---|---|
| `subject_definitions` | one line per tracked item: `<Subject N>` (reusable visible content: people, objects, scenes, clothing, styles, poses; may be defined by several assets), `<Picture N>` (an image used as a concrete first frame / keyframe / last frame / storyboard anchor), `<Video N>` (whole-video relations: editing source, continuation, camera/cut structure), `<Audio N>` (standalone audio or a reference video's soundtrack; bind to a speaker: `<Audio 1> is the voice-timbre reference for <Subject 1> (S1).`) |
| `summary` | one short paragraph prefixed by the task types in brackets joined with ` + `: `keyframe completion`, `reference generation`, `video editing`, `video continuation`, `audio reuse`, `audio reference` — e.g. `[video continuation + keyframe completion]`; edits start `The target video is an edited version of <Video 1>.` |
| `retention_analysis` | one line per label with fixed markers — visual: `fully_preserved`, `partially_preserved`, `attribute_transfer`, `weak_reference`; audio: `fully_copy`, `partially_copy`, `reference`, `weak_reference`; e.g. `<Subject 1> (appears in [Shot 1], [Shot 3]): fully_preserved - ...`, `<Audio 1>: fully_copy - ...` |
| `detailed_description` | the body (replaces `integrated_multimodal_description`): style in one or two sentences **before** `[Shot 1]`; then timestamped shots with labels where they apply (`the shot begins from <Picture 1>`, `the shot's keyframe corresponds to <Picture 2>`, `the shot ends on <Picture 3>`); `<Subject N> (Sx)` when a referenced subject speaks; normally 350–500 English words |
| `overall_soundscape` | as in base mode; cite `<Audio N>` copy/reference relations only in the matching lane |
| `non_diegetic_music` | as in base mode, e.g. `<Audio 2> is directly reused as the complete audience-only score.` |

Labels keep their meaning across all six sections; `<Video N>` and `<Audio N>` are numbered independently.
**In ComfyUI the tags bind to the `ref_images` / `ref_videos` / `ref_audios` slots in connection order** —
images first, then videos (each soundtrack's `<Audio j>` right before its `<Video k>`), then standalone audio;
ordinals are 1-based per type. A `<Picture N>` with no connected image is dangling. Reused reference
dialogue keeps the exact source words and language inside `<d>`; `[unclear]` for unintelligible spans.
"Assign each reference a job" (identity, style, motion, camera, voice); for a reference sheet give the
sheet its own `<Picture N>` and point shots at its panels.

**Official full-reference example (abridged from the ref guide's complete example; the sections are verbatim):**

```
subject_definitions:
<Subject 1> is the coffee-shop environment in <Picture 1>, featuring an exposed brick wall, an orange tufted sofa with patterned pillows, a neon sign, and a wooden coffee table.
<Subject 2> is the fluffy white Samoyed in <Picture 2>, <Picture 3>, and <Picture 4>, with thick white fur, pointed ears, a dark nose, and a curved tail.
<Subject 3> is the young blonde woman in <Video 1>, with long blonde hair and a light-pink button-down shirt with rolled-up sleeves.
<Subject 4> is the young man in <Video 2>, with short wavy brown hair and a dark-grey hoodie with drawstrings.
<Audio 1> is the voice-timbre reference for <Subject 3> (S1), containing a spoken English vocal layer.

summary:
[reference generation + audio reference] The target video shows <Subject 3> eating a cookie in <Subject 1>. <Subject 4> enters with <Subject 2>, which lunges toward the cookie. The three-shot exchange uses <Audio 1> as the voice-timbre reference for <Subject 3> and ends with a canned audience laugh.

retention_analysis:
<Subject 1> (appears in [Shot 1], [Shot 2], [Shot 3]): fully_preserved - the exposed brick wall, orange tufted sofa, patterned pillows, neon sign, and wooden coffee table are retained.
<Subject 2> (appears in [Shot 1], [Shot 2]): fully_preserved - the Samoyed's thick white fur, pointed ears, dark nose, and curved tail are retained.
<Subject 3> (appears in [Shot 1], [Shot 2], [Shot 3]): fully_preserved - the blonde woman's identity, long hair, and light-pink shirt are retained.
<Subject 4> (appears in [Shot 1], [Shot 2]): fully_preserved - the young man's short wavy brown hair and dark-grey hoodie are retained.
<Audio 1>: reference - its vocal timbre guides the dialogue delivery of <Subject 3> without copying the original signal.

detailed_description:
The target video uses a realistic multi-camera sitcom style with warm indoor lighting.
[Shot 1] A medium shot establishes <Subject 1>, ... <Subject 3> (S1) jerks her hand back and, using the clear youthful voice timbre referenced from <Audio 1>, exclaims with light annoyance, <d>[English] Hey! Watch your dog!</d> ...
[Shot 2] At 00:03.000, the shot cuts to a close-up of <Subject 4> (S2), ... <d>[English] He just likes cookies more than me.</d> ...
[Shot 3] At 00:05.000, the shot cuts to a close-up of <Subject 3> (S1), ... <d>[English] Well, he has good taste at least.</d> ... A classic canned audience laugh begins immediately after the line and continues through the final frame.

overall_soundscape:
Soft indoor coffee-shop room tone continues throughout the scene.

non_diegetic_music:
N/A
```

The `multiframe-ref2va` recipe carries a complete, unabridged six-section prompt (the official Multiframe
template's) and is the canonical in-repo example. The Comfy-Org T2V/I2V/R2V templates themselves ship
free-form storyboards ("Scene overview ... [0s-1.5s] Shot 1 ... Camera: ... Audio: ..."); they work, but
the Context-IR format is what the model was trained on, and the validator warns when the section markers
are missing.

What hurts: negative phrasing; mood words in `non_diegetic_music`; dialogue or music inside
`overall_soundscape`; tags for unconnected inputs; cut times out of order or past the duration;
transitions other than cuts unless asked. Ref2VA "output is very sensitive to prompt wording".

## Settings that matter (the validator enforces the hard ones)

- **Frames:** `length` sits on the **17k+5 grid at 24 fps** — 5, 22, 39, …, **124 = 5 s**, …, 362 ≈ 15 s.
  Trained range ~124–362; the templates compute it from seconds as
  `max(5, round(s*24)) + (5 - (max(5, round(s*24)) % 17)) % 17`. Guide clips and control clips are
  cropped / held to the same grid. **fps is 24**, always; anything else desyncs the audio.
- **Canvas:** multiples of **32**; native = **768 px short edge, capped at the 768×1344 pixel area**.
  1344×768 (0.98 MP) is the official 768p — render faces and text there. The templates' 0.4 MP preview is
  864×480 (16:9) / 640×640 (1:1). Never the "1.0 MP" step (1376×768 is over the cap). I2V: the first frame
  is stretched to the canvas, so the canvas must have the image's aspect (the recipes derive it).
- **No CFG, no negative:** `BasicGuider` only; the checkpoints are CFG-distilled.
- **Steps:** 20 base (25 if a reference drifts); 8 with the fl2v 8-step LoRA; 4 with the 4-step LoRAs;
  FastH3 exactly 8. Simple content holds at 12–16; fine texture keeps improving to ~50; audio is weakest
  below 12.
- **Sampler / scheduler:** `res_multistep` + `simple`; **`beta` or `normal` for reference-heavy prompts**
  (R2V template note). One `SamplerCustomAdvanced` per clip — multistep history does not survive a split.
- **Shift:** nothing to set on the base checkpoints (12 / 3 from the model class). FastH3 needs
  `MiniMaxH3SigmaShift` 10 / 3.
- **References:** ≤ 9 images, ≤ 3 videos (2–15 s each, ≤ 15 s total, ≥ 5 frames), ≤ 3 soundtracks,
  ≤ 3 audio clips (2–15 s each), 12 files total; slots `ref_images.ref_image_N` … from 0, contiguous.
  `ref_image_size` **match** (scaled to the canvas, fast) or **max** (2048 px short edge, stronger identity,
  "several times slower"). Without `vae` wired, references only reach the text encoder.
- **Guides:** `frame_idx = round(seconds × 24)`, negative from the end; index + guide length ≤ `length`;
  chain `positive → positive`, last guide into `BasicGuider`. Guides condition, they do not seed the latent.
- **LoRA ↔ build:** fl2v LoRAs on the FL2VA checkpoint, ref2v on Ref2VA; match the build a LoRA was
  distilled on and prefer **pruned** builds — a full-build LoRA's adaln tensors have no match in a pruned
  checkpoint. The turbo LoRAs carry no adaln tensors and load on either build.
- **Text encoder / VAE pairing:** every build uses `CLIPLoader` type `minimax` with
  `qwen3vl_32b_minimax_h3_*` (NVFP4 default, no Blackwell needed), `minimax_h3_video_vae_*` for video and
  `minimax_h3_audio_vae_fp32` for audio — two `VAELoader`s, `VAEDecode` + `VAEDecodeAudio` into
  `CreateVideo`. No clip_vision model exists: the encoder reads the images.
- **ControlNet:** `ModelPatchLoader` (model_patches/) → `MiniMaxH3FunControlNetApply` with the **video**
  VAE; strength 1.0, raise above 1 only if the control drifts; a longer control clip is trimmed from the
  start, a shorter one holds its last frame.
- **Attention:** default PyTorch with int8_convrot files. Comfy Kitchen attention crashes with int8_convrot
  (pair it with pruned_bf16 — which does not fit our disk). Sage ≈ 2× faster; INT8 Sage morphs / garbles text.

## When it goes wrong

| symptom | fix |
|---|---|
| LoRA shape-mismatch warnings | LoRA on the wrong checkpoint or a full-build LoRA on a pruned file |
| "alignment error" crash | Comfy Kitchen attention + int8_convrot: use default attention |
| metallic / underwater audio with turbo | fixed in core 2026-08-07; the 0.35.0 image is newer |
| soft faces in wide shots | render at 1344×768; do not latent-upscale |
| reference barely applied, pose drifts | Lightning off, 20–25 steps, `ref_image_size` max |
| "1.0 MP" rejected / mushy | 1376×768 is over the cap: 1344×768 |
| voiceover lip-synced onto the character | name the narrator, state whose lips stay closed, or feed the line as `<Audio N>` with `fully_copy` |
| red cheek patches (Ref2VA) | unresolved upstream; a realism LoRA helped one user |
| FastH3 grid artifacts | 8 steps, shift 10/3, no Ref2VA |
| ValueError on a reference / guide clip | ≥ 5 frames; frame_idx + guide frames ≤ length; audio 2–15 s |
| out of disk | one checkpoint at a time (40.1 GB with encoder + VAEs) |
| ROCm "CUDA error: invalid argument" on the VAE | `--disable-smart-memory` |
| H3 breaks after an update | remove `ComfyUI-MiniMaxH3-Cache` (single-source claim) |

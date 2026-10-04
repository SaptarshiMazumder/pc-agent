# FLUX.2 — the agent's guide

Black Forest Labs' second image family (dev 2025-11, klein 2026-01): one architecture for generation and editing, so there is no Fill/Depth/Canny/Redux line — references replace them. **dev** is a 32B guidance-distilled model; **klein** comes as 4B and 9B, each as a 4-step **distilled** model and an undistilled **base** with real CFG, plus a 9B **KV** model for fast multi-reference edits. Everything below was read from the official Comfy-Org templates, docs and BFL cards on 2026-10-01; `profile.json` holds the exact files, numbers and sources.

## When to pick it, and which recipe

- **Best still, 24 GB card + lots of system RAM:** `t2i-dev` (fp8 transformer 35 GB + Mistral encoder,
  "up to 4MP", brand-colour hex codes, the strongest text rendering in the KB). Swap the 36 GB bf16
  encoder for `mistral_3_small_flux2_fp8` or set `CLIPLoader.device = cpu` on a 24 GB card.
  `t2i-dev-turbo` does it in 8 steps.
- **Edit by instruction with the quality model:** `edit-dev` (one reference), `edit-dev-multi` (two or
  more, "up to 10 images", each named "Reference Image N"), `-turbo` variants at 8 steps.
- **Small cards:** the **klein 4B** line: `t2i-klein-4b-distilled` (4 steps, "under a second", ~8 GB)
  or `t2i-klein-4b-base` (20 steps, real negative, more diversity); `edit-klein-4b-distilled` / `-base`
  (+ `-multi`).
- **Better klein, 16 GB+:** the **9B** line: `t2i-klein-9b-base` / `t2i-klein-9b-distilled`; `edit-klein-9b-base` / `-distilled` (+ `-multi`).
- **Fast multi-reference edits:** `edit-klein-9b-kv` / `-multi` — FluxKVCache caches the reference tokens,
  1.4x faster at one reference, 2.2x at four.
- **Not this family** for: masked inpaint (no Fill model — describe the change instead), outpainting on
  this image (the template needs DrawMaskOnImage/ColorMatch, absent from v0.35.0), structural control
  (no Depth/Canny), more than 4 references on klein, negative prompts on dev.

Stage composition the user usually wants: `t2i-dev` → `edit-klein-9b-kv` for quick iterations on the
result; a product shot + a logo → `edit-dev-multi`; any still → Wan/LTX for motion.

## What the recipes are (one line each)

| recipe | needs | does |
|---|---|---|
| `t2i-dev` / `t2i-dev-turbo` | prompt | 1024² still, FluxGuidance 4, 20 steps (8 with the LoRA) |
| `edit-dev` / `-turbo` | reference image | edit at the reference's size (1 MP) |
| `edit-dev-multi` / `-turbo` | 2 references | compose/apply across images; fp8 encoder, flux2-vae |
| `t2i-klein-4b-base` / `t2i-klein-9b-base` | prompt (+ optional negative) | undistilled, CFGGuider 5, 20 steps |
| `t2i-klein-4b-distilled` / `t2i-klein-9b-distilled` | prompt | 4 steps, cfg 1 |
| `edit-klein-{4b,9b}-{base,distilled}` (+ `-multi`) | 1-2 references | klein edits; references on both CFG branches |
| `edit-klein-9b-kv` / `-multi` | 1-2 references | KV-cached fast edits, 4 steps |

Disk: dev fp8 + Mistral bf16 + VAE ≈ 71 GB (fp8 encoder: 54 GB); klein 9B fp8 + Qwen3-8B fp8 ≈ 18 GB;
klein 4B fp8 + Qwen3-4B ≈ 12 GB. Pending: GGUF routes (needs the ComfyUI-GGUF pack); outpainting
(newer nodes).

## Prompting

**Negative prompt:** none on dev (distilled — the graphs have no negative input). klein base wires a real
negative through `CFGGuider` (templates leave it empty; BFL gives no guidance for it). Say what you
want: "no people" → "empty, deserted"; "not dark" → "brightly lit".

**Positive:** `[SUBJECT], [LOCATION], [STYLE], [CAMERA SETTINGS], [LIGHTING], [COLORS], [EFFECT]` — "Start
short. Add only what changes the image." Most important elements first. Photoreal: name camera and lens
("Shot on Hasselblad X2D, 80mm lens, f/2.8"), eras ("2000s digicam aesthetic"), hex colours ("walls in
hex #C4725A"); put text to render in quotes with placement, font and colour. The ComfyUI dev path
pads/crops to 512 tokens; `(word:1.2)` weights do nothing on dev.

**Editing:** "FLUX.2 understands the context of your image and applies edits while preserving what you
didn't ask to change" — "Change it to Night"; "Remove all of the sprinkles while keeping the rest of the
image unchanged"; "Change the cow's white fur to the color #8bc4bb". Be specific about what changes and
what stays; klein base likes long preservation clauses ("Keep the person in the exact same position,
scale, and pose. Maintain identical camera angle, framing, and perspective").

**Several references:** give each image a role by number in load order — "Apply the design from
Reference Image 1 onto objects in Reference Image 2"; "the woman in image 2 sitting on the swing in
image 1 … all in the style of image 4".

What hurts: negations; unnamed roles for multiple references; many edits at once without preservation
clauses; very long prompts; unquoted text.

## Settings that matter (the validator enforces the hard ones)

- **Loaders:** `UNETLoader` + `CLIPLoader` type **`flux2`** (one file: `mistral_3_small_flux2_*` for dev,
  `qwen_3_4b*` for klein 4B, `qwen_3_8b*` for klein 9B) + `VAELoader` with **`flux2-vae.safetensors`** or
  **`full_encoder_small_decoder.safetensors`** (1.4x faster decode). Never the FLUX.1 `ae.safetensors`
  (16 vs 128 channels), never `CheckpointLoaderSimple` (no FLUX.2 checkpoints load).
- **Latent + schedule:** `EmptyFlux2LatentImage` (multiples of 16) and **`Flux2Scheduler` with the same
  width/height** — its sigmas depend on the pixel count; edit graphs feed both from `GetImageSize`.
  Sampler: `RandomNoise` + `KSamplerSelect euler` + `SamplerCustomAdvanced`.
- **Guidance:** dev → `FluxGuidance` 4 into `BasicGuider`, no negative. klein → `CFGGuider`: **base
  cfg 5 + a negative prompt, 20 steps**; **distilled cfg 1 + `ConditioningZeroOut`, 4 steps** (cfg > 1
  breaks it). No `FluxGuidance` on klein.
- **References:** scale each to **1.0 megapixel**, `VAEEncode`, one `ReferenceLatent` per image, chained;
  with `CFGGuider` the reference goes on **both** the positive and the negative branch. Output size =
  reference 1's size.
- **Turbo LoRA:** dev only, strength 1, **steps 8**, use the `_comfyui` conversions.
- **KV model:** `UNETLoader → FluxKVCache → CFGGuider`.
- **Precision:** fp8 saves VRAM everywhere, speeds up only RTX 40+; NVFP4 files need RTX 50.

## When it goes wrong

| symptom | fix |
|---|---|
| OOM loading the encoder / first step | fp8 or fp4 Mistral; `CLIPLoader.device = cpu`; more system RAM |
| "clip input is invalid: None" | you loaded a checkpoint; use UNETLoader + CLIPLoader(flux2) + VAELoader |
| channel-mismatch at decode | FLUX.1 VAE or EmptySD3LatentImage in a FLUX.2 graph |
| soft / wrong-contrast output after a resize | Flux2Scheduler size ≠ latent size |
| klein base noisy at 4 steps, distilled burnt | base: 20 steps cfg 4-5; distilled: 4 steps cfg 1 |
| reference ignored / wrong output size | ReferenceLatent missing (or on one branch only); size from GetImageSize |
| Turbo output poor | steps 8; the `_comfyui` LoRA file |
| 401 on 9B files | gated BFL repo — accept the agreement; Comfy-Org only hosts the 9B encoder + VAE |
| negative prompt does nothing | it is dev (distilled) — rephrase positively |
| NVFP4 slow / fails | Blackwell only; use fp8 |

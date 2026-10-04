# Classic upscale models + hires fix — the agent's guide

Pixel-space super-resolution networks (ESRGAN / Real-ESRGAN, SwinIR, DAT, SPAN, HAT, RCAN...) that ComfyUI loads through spandrel with two core nodes: `UpscaleModelLoader` → `ImageUpscaleWithModel`. They are **enhancement stages**: another stage's image or video goes in through `@image` / `@video`, a 2×/4× version comes out in seconds with no VRAM to speak of. Plus the two **hires-fix fragments** that add a second sampler pass to an image model's stage. No prompt. Read from the ComfyUI source, the Comfy templates and OpenModelDB on 2026-10-01; `profile.json` has the catalogue with exact bytes, URLs

## When to pick it, and which recipe

- **Fast, deterministic enlargement** of a finished still — the default "make it bigger" step. It cannot invent
  detail ("It can't create new detail like diffusion can, but it's much faster"); for restoration or invented
  texture use SeedVR2 (or a diffusion hires fix).
- **Which model:** photo/realistic → `upscale-image-photo` (RealESRGAN_x4plus, the template default); AI render that should look crisp → `upscale-image-photo-ultrasharp` (UltraSharp; NC); anime/cartoon/mixed → `upscale-image-anime` (UltraSharpV2, DAT2 — slow transformer, NC); anime/line art fast → `upscale-image-anime-animesharp`. The `upscale_model` port swaps any catalogue file; `.pth` files load fine on the box (the offline validator only names `.safetensors`).
- **2× or an exact size:** `upscale-image-2x` — 4× model then `ImageScaleBy 0.5` (template tip: "Upscale 4x
  first, then use a resize node"); the supersample also averages GAN noise.
- **Video:** `upscale-video-frames` is the official Real-ESRGAN video template (per frame, fps/audio preserved);
  `upscale-video-frames-2x` adds the 4×→2× downscale that damps shimmer. Both are per-frame with no temporal
  model — the flicker rule warns; **prefer SeedVR2 for any video deliverable**.
- **Hires fix:** `hires-fix-latent` (LatentUpscale 1.5× → KSampler denoise 0.5; a fragment spliced after any
  sampler, no extra model) or `hires-fix-pixel` (ESRGAN 4× → resize to target → VAEEncode → KSampler denoise
  0.5; sharper base). Both carry the official SD1.5 first pass so the file validates standalone; in a pipeline the
  `fragment.attach` map says which upstream sockets replace it.
- **Not this family** for: temporal coherence, inventing content, face restoration (those checkpoints are rejected:
  "Upscale model must be a single-image model"), RGBA on the pinned 0.35.0 image (crash fixed in 0.38.0 — feed RGB).

Stage composition: FLUX/Qwen/SDXL still → `upscale-image-photo` ×4 → print; SDXL stage → `hires-fix-latent`
fragment → 1.5× refined still.

## What the recipes are (one line each)

| recipe | model | does | cost |
|---|---|---|---|
| `upscale-image-photo-ultrasharp` | 4x-UltraSharp (NC) | ×4 crisp detail for renders | seconds |
| `upscale-image-anime` | 4x-UltraSharpV2 DAT2 (NC) | ×4 anime/cartoon/realistic | slower (transformer) |
| `upscale-image-anime-animesharp` | 4x-AnimeSharp (NC) | ×4 anime, text | seconds |
| `upscale-image-2x` | RealESRGAN_x4plus | ×4 then ×0.5 lanczos = ×2 | seconds |
| `upscale-video-frames` / `-2x` | RealESRGAN_x4plus | per-frame ×4 (/ ×2) with audio+fps through | RAM-bound on long clips |
| `hires-fix-latent` | checkpoint only | 1.5× latent + second pass @0.5 | one img2img pass |
| `hires-fix-pixel` | RealESRGAN_x4plus + checkpoint | ESRGAN 4× → resize → encode → second pass @0.5 | one img2img pass |

## Prompting

None for the upscalers. In a hires fix the second KSampler reuses pass 1's positive/negative (both official
examples); the "different prompt" example swaps checkpoint and prompt (8 steps, cfg 13, denoise 0.35).

## Settings that matter (the validator enforces the hard ones)

- **The model fixes the scale** (1×/2×/4×). Target ≠ model scale → resize after (`ImageScaleBy` / `ImageScale`,
  width or height 0 keeps aspect; 'area' for downscaling, 'lanczos' for upscaling).
- **Tiling is internal:** 512 px / 32 overlap, halved on OOM down to 128 px — nothing to set.
- **Hires-fix denoise ~0.5** after a latent upscale (interpolation is blurry); the pixel path can go lower.
  `LatentUpscale` width/height in steps of 8. ESRGAN output must go through `VAEEncode` before the second sampler.
- **Model vs source:** classicalSR SwinIR, DAT/HAT pretrains, 4xLSDIR are clean-input-only; UltraSharp-class
  halos already-sharp renders; realSR/Nomos/RealESRGAN for degraded sources.
- UltraSharpV2, AnimeSharp(V4), ClearReality, Remacri
- **fps** wired through from GetVideoComponents on video.
- Several OMDB downloads live on Mega/Icedrive/Drive and some filenames are inferred (marked VERIFY in
  `profile.files`): prefer the authors' HF repos and check sha256.

## When it goes wrong

| symptom | fix |
|---|---|
| video shimmers | 4×→2× supersample helps; SeedVR2 for a real fix |
| "Upscale model must be a single-image model." | not an SR model — load one |
| unsupported arch | `pip install spandrel_extra_arches` |
| got 4× wanted 2× | ImageScaleBy 0.5 after, or a 2× model |
| halos on an AI render | softer model (RealESRGAN_x4plus, Nomos DAT, SwinIR-realSR) |
| artifacts amplified | clean-input model on a JPEG source — use a realSR model |
| soft hires fix | denoise ~0.5 after LatentUpscale, or the pixel path |
| crash on transparent PNG | feed RGB on 0.35.0 |
| very slow | transformer arch — use ESRGAN/SPAN/compact |

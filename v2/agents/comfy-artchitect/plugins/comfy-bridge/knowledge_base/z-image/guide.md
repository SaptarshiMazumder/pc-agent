# Z-Image / Z-Image-Turbo — the agent's guide

Open-weights 6B single-stream DiT from Alibaba Tongyi Lab, free. The fastest good photoreal text-to-image on the box: **Turbo** does 8 steps with no CFG and renders Chinese and English text accurately. Everything below was read from the official templates, model cards and ComfyUI source on 2026-10-01; `profile.json` holds the exact files, numbers and sources.

## When to pick it, and which recipe

- **Default T2I, any card from 16 GB:** `t2i-turbo` — 8 steps, cfg 1, shift 3, ~12 GB bf16 model +
  8 GB Qwen3-4B encoder. Officially "Very High" visual quality but **low seed diversity** and **no
  negative prompt** (the model is CFG-free). Bilingual text rendering is a headline feature.
- **You need a real negative, diversity across seeds, or a LoRA base:** `t2i-base` — the non-distilled
  Z-Image: 25 steps (official 28–50), cfg 4 (3–5), negatives "strongly recommended". 3–6× slower.
- **Small card:** `t2i-turbo-int8` / `t2i-base-int8` — 6.2 GB int8 ConvRot files + 5.6 GB fp8 encoder.
  VRAM floor NOT FOUND; the int8 templates are dated 2026-07 and loading on the pinned 0.35.0 image is
  not verified — fall back to bf16 if the load fails.
- **Structural control (canny / HED / depth / pose / MLSD):** `control-turbo-fun-union-canny` (give it the
  photo, Canny runs in-graph) or `control-turbo-fun-union-map` (give it a ready map). Alibaba PAI's Fun
  ControlNet-Union is a **model patch** on Turbo only; the output is sized from the control image.
- **Upscale / img2img:** `upscale-turbo-2k` — ESRGAN ×4 → ×0.5 → Turbo refine at denoise 0.33, 5 steps.
  Also the pattern for any Turbo img2img (VAEEncode in place of the empty latent, denoise ≤ 0.35).
- **Not this family** for editing: **Z-Image-Edit and Omni-Base are NOT released** (official Model Zoo
  "To be released", no HF repo on 2026-10-01). Route edits to qwen-image (Edit-2511 / 2509) or hidream
  (E1.1). No IP-Adapter, no inpaint template, no GGUF found.

Stage composition: Turbo stills → wan-2.2 `i2v-14b` for motion; a depth/pose map from another stage →
`control-turbo-fun-union-map`; any family's output → `upscale-turbo-2k`.

## What the recipes are (one line each)

| recipe | needs | does |
|---|---|---|
| `t2i-turbo` | prompt | 1024² in 8 steps, no CFG, no negative |
| `t2i-turbo-int8` | prompt | same on the 6.2 GB int8 file + fp8 encoder |
| `t2i-base` | prompt (+ negative) | non-distilled, 25 steps cfg 4, PNG out |
| `t2i-base-int8` | prompt (+ negative) | base model on the int8 file |
| `control-turbo-fun-union-canny` | source photo | Canny → Fun Union v1 patch → 8-step Turbo, output = photo size |
| `control-turbo-fun-union-map` | ready control map | same, you supply HED/depth/pose/MLSD/canny |
| `upscale-turbo-2k` | image | ESRGAN x4 → x0.5 → Turbo refine (denoise 0.33) → ~2K |

Disk: Turbo or base bf16 (12.3 GB) + encoder (8.0 GB) + VAE (0.3 GB) ≈ 21 GB; the control patch adds 3.1 GB.

## Prompting

**Negative prompt:** none on Turbo — the recipes feed `ConditioningZeroOut` of the positive. Put exclusions
in the positive ("no text, no watermark, no logos"). On `t2i-base` a real negative works and is
recommended by the publisher; the template ships it empty.

**Positive prompt:** long, dense natural-language description. Official Turbo example: *"Young Chinese
woman in red Hanfu, intricate embroidery. Impeccable makeup, red floral forehead pattern. Elaborate high
bun, golden phoenix headdress, red flowers, beads. Holds round folding fan with lady, trees, bird. Neon
lightning-bolt lamp, bright yellow glow, above extended left palm. Soft-lit outdoor night background,
silhouetted tiered pagoda (西安大雁塔), blurred colorful distant lights."* Mixed Chinese/English is fine.
Community structure: shot → subject → appearance → clothing → environment → lighting → mood → style →
technical notes, 80–250 words of prose, not comma tags.

**Text in the image:** quote the exact string, keep each text chunk in one language, state placement
("large white title at the top"), add "no additional text except the title, no random numbers or watermarks".

What hurts: cfg > 1 on Turbo (burns), 30+ steps on Turbo, relying on the negative box, poetic phrasing,
ambiguous clothing descriptions. The publisher's Prompt Enhancer is not in any ComfyUI template — enrich
the prompt yourself.

## Settings that matter (the validator enforces the hard ones)

- **Text encoder:** `CLIPLoader` type **`lumina2`** on `qwen_3_4b*.safetensors`. Any other type is unsupported.
- **VAE:** the FLUX `ae.safetensors` (Z-Image's latent is Flux-type).
- **shift:** `ModelSamplingAuraFlow` **3** everywhere — the node default 1.73 is wrong for Z-Image.
- **Turbo:** steps 8, **cfg 1**, `res_multistep` / `simple`. cfg above 1 burns and doubles the passes.
- **Base:** steps 25–50, cfg 3–5, same sampler.
- **ControlNet:** Turbo only; v1 union or a `*-2.1-*-8steps` file at 8 steps — the 2.0/2.1 non-8steps unions
  lose the acceleration and blur. Strength 0.65–1.0 (template 1). Patch lives in `models/model_patches`.
- **Sizes:** multiples of 16; 1024² default; official window 512² to 2048² area; control images over
  ~2500 px must be scaled first (the template's bypassed `ImageScaleToMaxDimension` lanczos 1024).
- **Upscaler denoise:** 0.15–0.25 subtle, 0.25–0.35 creative, above 0.35 artifacts.

## When it goes wrong

| symptom | fix |
|---|---|
| negative prompt ignored | Turbo is CFG-free: ConditioningZeroOut, cfg 1, exclusions in the positive — or `t2i-base` |
| burnt / oversaturated, slow | cfg back to 1 on Turbo |
| flat detail vs the template | shift 3, not the 1.73 default |
| ControlNet blurry, needs many steps | v2.0 / 2.1 non-8steps union: use v1 or a `*-8steps` build |
| control output wrong size / OOM | scale the control image to ~1024 px, multiples of 16 |
| base LoRA breaks Turbo | stack `z_image_turbo_distill_patch_lora_bf16` (community-documented) |
| int8 / nvfp4 fails to load | bf16 files on the pinned image |
| "where is Z-Image-Edit" | not released; qwen-image or hidream for edits |
| upscaler artifacts | denoise ≤ 0.35, detailed caption as prompt |

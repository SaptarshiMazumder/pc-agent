# Qwen-Image family — the agent's guide

Alibaba Qwen's 20B MMDiT image family, free: **Qwen-Image** (Aug 2025) and **2512** (Dec 2025) for text-to-image, **Qwen-Image-Edit / 2509 / 2511** for instruction editing with up to three input images, **Layered** for RGBA layer decomposition, and a ControlNet ecosystem (InstantX, DiffSynth, Alibaba PAI). The family's headline is **text rendering** (English, Chinese, Korean, Japanese; posters, slides, signs) plus editing that preserves "font, size, and style". Everything below was read from the official templates, docs and cards on 2026-10-01; `profile.json` holds the exact files, numbers and sources.

## When to pick it, and which recipe

All fp8 diffusion files are ~20.4 GB; the reference is an RTX 4090D 24 GB at 86 % VRAM, ~94 s per 1328²
image (Qwen-Image fp8, 20 steps), ~55 s → ~34 s with an 8-step LoRA.

- **A real person from their photo (photoreal), whatever the job:** task `person-from-reference`, three
  steps: `t2i-2-1-person-from-reference` (the shot, with their body read off their photo by a form —
  ethnicity, skin, body size, bust, belly, hair; the photo is never rendered from) → `head-swap-2-1` (target
  = that still, face = their photo; the BFS head-swap LoRA keeps the still's gaze and expression), both at
  1024x1536, fixed (smaller or larger, the face came out long and narrow; a video model resizes its first
  frame by itself). Nothing after them: a Z-Image pass
  changed her face and made the skin plastic, an upscaler added nothing. Every later picture or video of
  them reads the swapped still. The still and the swap are FIXED recipes (tested templates): only the shot, size and
  images are set — a LoRA, another seed or a reworded swap prompt broke them in tests, and the design
  check refuses them. Tested 2026-10-07: the only route that kept her face, hair, glasses and figure; editing her
  own photo (2511, FLUX.2 klein) changed her face or body, and a Z-Image Turbo first still drew her slim
  0/4. Angles: make each from the final still (`multi-angle`), then `head-swap-2-1` again with the photo
  whose head angle is closest.
- **T2I, text-heavy or photoreal people:** `t2i-2512` (50 steps, cfg 4, official Chinese negative).
  Faster: `t2i-2512-lightning-4step` (4 / 1); draft: `t2i-2512-turbo-2step` (2 steps, third-party LoRA).
- **T2I, the original Aug-2025 look:** `t2i` (20 / 4) or `t2i-lightning-4step` (4 / 1, fp8-distilled LoRA).
- **Edit a photo:** `edit-2511` (40 / 3) — least drift, best character consistency; with a reference image
  (material / object / person transfer): `edit-2511-multi-image`; fast: `edit-2511-lightning-4step`.
  Alternatives: `edit-2509*` (map inputs, 1–3 images; the template's default is the 4-step LoRA) and the
  legacy single-image `edit*`.
- **Structural control:** `control-instantx-union` (canny / soft edge / depth / pose, ready map, 20 / 2.5;
  `-lightning-4step` for 4 / 1), `control-2512-fun-union` (7 conditions on 2512, Canny in-graph, 50 / 4),
  `control-diffsynth-patch-canny` / `-map` (2.3 GB model patch), `control-union-lora` (0.9 GB LoRA +
  ReferenceLatent, 7 control types).
- **Inpaint / outpaint:** `inpaint-instantx` (painted mask, original pixels composited back) and
  `outpaint-instantx-lightning-4step` (ImagePadForOutpaint canvas).
- **Layer decomposition:** `layered` — RGBA layers from one image (40.9 GB bf16 file; shift 1; slow).
- **Qwen Image 2.1 runs on Comfy Cloud** (t2i and two-image edits, 2026-10-07); `background-removal-2-1`
  is still unrun.
- **Not this family** for IP-Adapter (NOT FOUND); 4K on 2.1; >3 inputs on 2509.

Stage composition: a Qwen T2I still → wan-2.2 `i2v-14b`; a Z-Image / Chroma still → `edit-2511` for text
fixes; a depth/pose map from any preprocessor → `control-instantx-union`.

## What the recipes are (one line each)

| recipe | needs | does |
|---|---|---|
| `t2i`, `t2i-lightning-4step` | prompt | Qwen-Image fp8 at 1328², 20/4 or 4/1 |
| `t2i-2512`, `-lightning-4step`, `-turbo-2step` | prompt | 2512 fp8, 50/4, 4/1 or 2/1 |
| `edit`, `edit-lightning-4step` | image + instruction | original Edit, TextEncodeQwenImageEdit, 1.5 MP, CFGNorm |
| `edit-2509`, `-lightning-4step`, `-multi-image-lightning-4step` | 1–3 images + instruction | TextEncodeQwenImageEditPlus, FluxKontextImageScale, CFGNorm |
| `edit-2511`, `-multi-image`, `-lightning-4step` | 1–2 images + instruction | 2511 fp8mixed, reference method index_timestep_zero, 40/3 or 4/1 |
| `control-instantx-union`, `-lightning-4step` | control map | ControlNetApplyAdvanced with VAE, output sized by the map |
| `control-2512-fun-union` | photo | Canny in-graph, GetImageSize → latent, 50/4 |
| `control-diffsynth-patch-canny` / `-map` | photo / map | model-patch ControlNet, 20/2.5 |
| `control-union-lora` | photo | union LoRA + ReferenceLatent, Canny in-graph |
| `inpaint-instantx` | image with painted mask | AliMama inpaint apply + SetLatentNoiseMask + composite |
| `outpaint-instantx-lightning-4step` | image | pad 200 px left/top/bottom, 4/1 |
| `layered` | image | RGBA layers (layers 2), shift 1 |
| `t2i-2-1`, `edit-2-1`, `background-removal-2-1` | pending | 2.1 int8 (7.3 GB), 25 steps cfg 1 |
| `t2i-2-1-person-from-reference` | their photo + the shot | describer form (Qwen3-VL, the same encoder) before the shot → 2.1 t2i, 30 steps |
| `head-swap-2-1` | target still + face photo | 2.1 + BFS head-swap LoRA v1.1, fixed 'head_swap:' prompt, 50 steps, target's size |

Disk: one fp8 model (20.4 GB) + Qwen2.5-VL-7B fp8 (9.4 GB) + VAE ≈ 30 GB; each extra model +20.4 GB;
Layered bf16 40.9 GB; 2.1 int8 7.3 GB + Qwen3-VL-8B int8 9.4 GB + RGBA VAE 0.7 GB.

## Prompting

**Negative prompt — official default is a single space** `" "` ("using an empty string if you do not have
specific concept to remove"); ignored at cfg 1 anyway. **For 2512 use the official negative verbatim:**

低分辨率，低画质，肢体畸形，手指畸形，画面过饱和，蜡像感，人脸无细节，过度光滑，画面具有AI感。构图混乱。文字模糊，扭曲

**Positive (T2I):** a full scene description, then **append the official suffix** `, Ultra HD, 4K, cinematic
composition.` (Chinese: `, 超清，4K，电影级构图.`). Put text to render in double quotes inside the scene:
*'A coffee shop entrance features a chalkboard sign reading "Qwen Coffee 😊 $2 per cup," with a neon light
beside it displaying "通义千问".'*

**Edit instructions:** one imperative sentence — *"Change the rabbit's color to purple, with a flash light
background."*; multi-image: *"The magician bear is on the left, the alchemist bear is on the right, facing
each other in the central park square."*; refer to inputs as **image 1 / image 2 / image 3** (2509/2511) or
`<image1>`… (2.1). InstantX ControlNets: add the word TEXT when text matters; InstantX inpainting: describe
the **whole** image descriptively, not instructively. Layered: describe the whole input incl. occluded parts.

What hurts: cfg > 1 under Lightning; dense/small text and hair detail under Lightning (use the base);
negatives at cfg 1; square canvases on 2511 (community); 4K on 2.1.

## Settings that matter (the validator enforces the hard ones)

- **Encoder / VAE pairing:** `CLIPLoader` type `qwen_image` with `qwen_2.5_vl_7b_fp8_scaled` + `qwen_image_vae`
  for Qwen-Image / Edit / 2509 / 2511 / 2512; Layered has its own VAE; 2.1 uses `qwen3vl_8b_*` + `qwen_image_2.1_vae_bf16`.
- **shift:** `ModelSamplingAuraFlow` **3–3.1** (Layered **1**; 2.1 has no node). "Increase the shift if you
  get too many blurry/dark/bad images." Node default 1.73 is wrong.
- **Lightning:** **cfg 1, steps = the LoRA's count (4 / 8 / 2)**. On the plain `qwen_image_fp8_e4m3fn` base
  only the **fp8-distilled** `Qwen-Image-fp8-e4m3fn-Lightning-4steps-V1.0` — the bf16-trained LoRAs give a
  **grid pattern** there.
- **CFGNorm strength 1** on every Edit graph; Edit graphs start from `VAEEncode(input)` (sets the size).
- **Sizes:** multiples of 16; native table 1328², 1664×928, 928×1664, 1472×1104, 1104×1472, 1584×1056,
  1056×1584. Edit/control inputs normalised to 1–1.7 MP; inpaint max 1536.
- **ControlNets:** InstantX and Fun-2512 in `models/controlnet` with the VAE connected; DiffSynth patches in
  `models/model_patches`; the union LoRA in `models/loras` + ReferenceLatent. Strength 0.8–1.0.
- **2.1:** cfg 1 (raise only for a negative), 25 steps (official 40–50), multiples of 32, `resolution` is a
  pixel budget for references (0 = native).

## When it goes wrong

| symptom | fix |
|---|---|
| grid pattern | fp8-distilled Lightning LoRA, or the bf16 / scaled-fp8 base |
| blurry / dark | raise shift (3.1 → up; community 12–13 as last resort) |
| 2511 fp8 noise | fp8mixed + bf16 2511 LoRA on ComfyUI ≥ 0.6.0, or lightx2v's fused checkpoint |
| 2511 washed-out backgrounds | non-square canvas (e.g. 832×1216) |
| edit ignores a reference (third-party repack) | FluxKontextMultiReferenceLatentMethod index_timestep_zero |
| negative does nothing | cfg is 1 |
| control file not listed | wrong folder (controlnet vs model_patches vs loras) |
| oversaturated skin (8-step) | Lightning V2.0 LoRA |
| distill model + LoRA broken | use one or the other |

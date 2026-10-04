# HunyuanVideo 1.5 — the agent's guide

Tencent's 8.3B open-weights video DiT (2025-11-20) Text-to-video and image-to-video at native 480p / 720p, 5 s @24 fps, with an optional super-resolution stage to 1080p. Silent (no audio). Everything below was read from the official templates, the Tencent repo and the Comfy-Org repack on 2026-10-01; `profile.json` has the exact files, numbers and sources.

*UNITED KINGDOM AND SOUTH KOREA" — the weights *and the outputs* may not be used there offer Wan 2.2 or LTX-2 instead.

## When to pick it, and which recipe

- **Pick it** for clean 720p T2V/I2V with strong camera-instruction following, legible in-video
  text in English or Chinese (glyph-aware ByT5 encoder), realistic/anime/3D styles, and when the user
  wants a 1080p deliverable from an open model (the SR stage). Light: a 16.7 GB fp16 model (8.3 GB fp8)
  that the Comfy blog places on "consumer GPUs (24GB VRAM)"; Tencent's reference code runs in 14 GB
  with offloading.
- **Not this family** for audio (none), first+last frame, reference/multi-subject or video editing
  (those are in HY-OmniWeaving, which is not native in ComfyUI), or clips longer than ~5 s per pass
  (121 frames is the only length the reference code does not warn about).

| recipe | needs | does |
|---|---|---|
| `t2v-720p` | prompt | 1280×720×121 @24 fps; template shortcut 20 steps / shift 7 / cfg 6 — set steps 50, shift 9 for Tencent's best-quality settings |
| `t2v-720p-sr1080p` | prompt | the same, then the template's 1080p SR group: latent upsampler + `1080p_sr_distilled` at cfg 1 / shift 2 / 8 steps split 4/4 → 1920×1080 (two 16.7 GB models in turn) |
| `t2v-480p-cfg-distilled` | prompt | the smallest T2V: 8.3 GB fp8 CFG-distilled file, cfg 1, shift 5, **50 steps (mandatory)** |
| `i2v-720p` | start image | 720p I2V with SigLIP clip-vision (always wire both `start_image` and `clip_vision_output`) |
| `i2v-720p-sr1080p` | start image | i2v-720p + 1080p SR (the SR node also gets vae, start_image, clip_vision_output) |
| `i2v-480p-step-distilled` | start image | the fast route: 480p step-distilled fp8, cfg 1, shift 7, 8 steps — "a single RTX 4090 ... within 75 seconds" |

Pending / not offered: 480→720 SR (files and settings exist, no template pairs them with the 480p
models); the lightx2v 4-step LoRA (single-source community settings only); 720p T2V CFG-distilled
("Coming soon"); the sparse-attention checkpoint (H-series GPUs only). Capybara v0.1 in the same
repack is a third-party derivative, not covered.

Stage composition the user usually wants: a still from an image model → `i2v-720p`; a quick look-dev
pass → `i2v-480p-step-distilled`; the final → `*-sr1080p`.

## Prompting

**Negative prompt: empty.** That is the official default (`--negative_prompt ''`) and what both
templates ship; at cfg 1 (distilled / SR) a negative is skipped anyway.

**Positive prompt — the official handbook's formulas:**

- T2V: `Subject + Motion + Scene + [Shot Type] + [Camera Movement] + [Lighting] + [Style] + [Atmosphere]`
- I2V: `Subject Motion Dynamics + Scene Motion Dynamics + [Camera Movement]` — the image is the first
  frame; describe what moves, do not re-describe what is visible.

Write long and concrete: "By writing longer and more detailed prompts, the generated video will be
significantly improved." Tencent's pipeline rewrites prompts with an LLM by default and warns of lower
quality without it; ComfyUI has no rewriter, so produce the long form yourself. Camera phrases the
model follows, verbatim from the Camera Movement Library: *The camera moves upward/downward · moves to
the left/right · moves forward · moves back · tilts up/down · pans to the left/right · circles around ·
rotates 360 degrees · follows · remains static.* Style words: photorealistic/cinematic, Film Noir,
cyberpunk, Low-Poly 3D, Chinese ink wash "Xieyi style", 2D animation, watercolor. For on-screen text
put the exact words in quotes (`"Hunyuan Video 1.5"`, `"混元视频 1.5"`).

What hurts: short prompts; re-describing the start image in I2V; counting on a negative at cfg 1.

## Settings that matter (the validator enforces the hard ones)

- **Encoders:** `DualCLIPLoader` type `hunyuan_video_15` with `qwen_2.5_vl_7b_fp8_scaled` +
  `byt5_small_glyphxl_fp16`. **VAE:** `hunyuanvideo15_vae_fp16` (32-ch, 16×) — never the HunyuanVideo
  1.0 VAE (`hunyuan_video_vae_bf16`, 16-ch, 8×; that one is Kandinsky's).
- **Tencent's table vs the template:** 720p T2V cfg 6 / shift **9** / **50** steps; 720p I2V 6 / 7 / 50;
  480p 6 / 5 / 50; every distilled or SR file cfg **1**. The templates run 20 steps and shift 7 for
  speed ("50 inference steps just take too long"). Both are recorded; the recipes ship the template
  values and expose `steps` / `shift` / `cfg` as ports.
- **Distilled files:** CFG-distilled → cfg 1 **and 50 steps** ("must use 50 steps to generate correct
  results"); 480p I2V step-distilled → cfg 1, shift 7, 8 or 12 steps (4 possible, lower quality);
  SR → cfg 1, shift 2, 6 steps (480→720) or 8 steps (720→1080).
- **length:** 4n+1, and 121 is the only tested value (the CLI warns otherwise). **fps** 24.
- **width/height:** multiples of 16; 1280×720 for the 720p files. The 480p files' exact Comfy size
  is NOT FOUND (node default 848×480). SR target 1920×1080 (multiples of 8).
- **Tiled decode:** if you use `VAEDecodeTiled`, `temporal_size` **4096** — Tencent's rule "to avoid
  generating artifacts" (the T2V template's bypassed node still carries 8).
- **OOM:** `UNETLoader.weight_dtype` fp8_e4m3fn (template note) or the `_fp8_scaled` files; the SR
  stage should use the **fp16** SR file (fp8 SR + fp16 VAE threw a size-98/64 error, HF #5).
- **EasyCache** is in the templates bypassed: faster, "but it will also sacrifice the video quality".

## When it goes wrong

| symptom | fix |
|---|---|
| artifacts after tiled decode | `temporal_size` 4096 |
| softer than Tencent's samples | steps 50 (+ shift 9 for 720p T2V) |
| burnt / over-guided on a distilled file | cfg 1; CFG-distilled also 50 steps; step-distilled 8/12 steps |
| out of memory | fp8_e4m3fn weight_dtype or `_fp8_scaled` file; tiled decode (4096); 480p routes |
| "Expected tensor to have size 98 ... got 64" in SR | use the fp16 SR file |
| black 1920×1072 SR frames | Comfy-Org SR files only; re-download if older than 2025-11-26 (Kijai's fp8-scaling fix) |
| I2V ignores the image | wire `start_image` **and** `clip_vision_output` (SigLIP, crop center); use the `_i2v_` file |
| "Attempting to generate {n} frames" | length 121 |
| faster but worse | EasyCache on — leave it bypassed for finals |

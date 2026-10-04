# Wan 2.2 — the agent's guide

Open-weights video family from Alibaba, free. The default pick for video when the user wants free: text-to-video, image-to-video, first+last frame, control video, camera moves, VACE reference, speech-to-video, character animation. Everything below was read from the official templates and model configs on 2026-10-01; `profile.json` holds the exact files, numbers and sources.

## When to pick it, and which recipe

- **Quality, ≥24 GB card:** the **14B pair** (`t2v-14b`, `i2v-14b`, `flf2v-14b`, `fun-*-14b`, `vace-14b`).
  Two models (high-noise expert then low-noise expert), two chained samplers, ~9 min for 5 s at
  640×640 on a 4090 — longer at 832×480 or 1280×720. 480p/720p native; 5 s (81 frames @16 fps) per pass.
- **Fast draft, or ≤16 GB:** the **5B** (`t2v-5b`, `i2v-5b`). One model, 720p @24 fps (1280×704×121),
  fits 8 GB with offloading. Visibly simpler results than the 14B.
- **Fast 14B:** the `-lightning` recipes: 4 steps, cfg 1, ~5× faster — and officially "loss of video
  dynamics". Good for look-dev iterations; render the final with the 20-step recipe, especially for
  big motion.
- **A longer clip than 5 s** is several 5 s passes (FLF2V chaining: last frame → next start), or the
  S2V/Animate extend subgraphs in 77-frame chunks. Never a single 161-frame latent.
- **Not this family** for: audio generation (none), >720p native, style-reference (VACE is object/background only).

Stage composition the user usually wants: a still from an image model (FLUX / Qwen / Z-Image) →
`i2v-14b`; a character sheet → `vace-14b` with the sheet as `reference_image`; storyboard frames →
`flf2v-14b` per shot.

## What the recipes are (one line each)

| recipe | needs | does |
|---|---|---|
| `t2v-14b` / `-lightning` | prompt | 5 s clip from text |
| `i2v-14b` / `-lightning` | start image | animates the image; no CLIP-vision (Wan 2.2 I2V has none) |
| `t2v-5b` / `i2v-5b` | prompt / start image | the small-card route, 720p @24 fps, needs the 2.2 VAE |
| `flf2v-14b` | start + end image | one motion between two frames (I2V models, no extra files) |
| `fun-inpaint-14b` | start + end image | same as FLF2V with the Fun InP weights |
| `fun-control-14b` | reference image + a prepared control video | pose/depth/canny-driven motion |
| `fun-camera-14b` | start image + a named camera move | Pan/Zoom/Rotate at a chosen speed |
| `vace-14b` | one reference image, optional control video | identity-locked generation; community-confirmed graph, no official 2.2 template |
| `s2v`, `animate` | pending | audio-driven talking; character animation (needs node packs) |

Disk: a 14B pair + encoder + VAE ≈ 35–38 GB; the rented box has 60 GB. fp16 pairs (57 GB) do not fit.

## Prompting

**Negative prompt — use exactly this, it is the official default and every template carries it:**

色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走

(vivid tones, overexposed, static, blurry, subtitles, painting, gray, low quality, JPEG artifacts, bad hands/face, deformed, fused fingers, motionless, cluttered background, three legs, crowd, walking backwards.)

**Positive prompt:** long and concrete. The publisher recommends *extending* prompts because detail
improves the video. Write **subject → action → camera move → light/style**, 80–120 words, English or
Chinese. For I2V the image carries the subject: spend the prompt on motion, camera and atmosphere.
Camera words the model follows: pan left/right, tilt up/down, dolly in/out, orbital arc, crane up,
tracking shot, slow zoom in/out, handheld, static camera; slow-motion, time-lapse. Lighting/look
words: volumetric dusk, harsh noon sun, neon rim light, backlit, lens flare, 16mm grain.

What hurts: short prompts (the model fills gaps with its defaults); words implying stillness; big
motion under a Lightning LoRA.

## Settings that matter (the validator enforces the hard ones)

- **VAE:** 14B → `wan_2.1_vae.safetensors`; 5B → `wan2.2_vae.safetensors`. Mixing them is the most
  reported Wan 2.2 error ("expected ... 36/48 channels").
- **shift** (ModelSamplingSD3): 5 for T2V/I2V, 8 for FLF2V/Fun/VACE/5B. The node default 3 is wrong for Wan.
- **cfg:** 3.5 (14B), 4 (FLF2V), 5 (5B); **1.0 under any distilled LoRA**. Higher → oversaturation.
- **Two experts, two samplers:** high (add_noise on, steps 0→10, leftover noise on) then low
  (add_noise off, 10→end). The LoRA for each expert goes on that expert's branch only.
- **length:** 4n+1 — 81 = 5 s at 16 fps, 121 = 5 s at 24 fps (5B). **fps** must match the model (16 / 24).
- **width/height:** multiples of 16; native 1280×720, 720×1280, 832×480, 480×832 (5B: 1280×704).
- **seed:** only the high-noise sampler's seed matters.

## When it goes wrong

| symptom | fix |
|---|---|
| channel-mismatch error | wrong VAE for the model (see above) |
| out of memory | 832×480 or 640×640, 81 frames, fp8; 5B on small cards |
| slow-motion, lifeless | drop the Lightning LoRA for the final; or 0.6–0.8 on high + cfg 2–3.5 high / 1 low, 8 steps |
| "key values don't match" | LoRA on the wrong expert or wrong task (T2V LoRA on I2V) |
| noise in the first VACE frames | TrimVideoLatent fed by trim_latent (the recipe has it) |
| I2V ignores the image | start_image not wired; t2v files used for i2v |
| first frame brighter | VAE first-frame effect; the S2V template duplicates and drops the first latent frame |
| oversaturated | cfg too high |

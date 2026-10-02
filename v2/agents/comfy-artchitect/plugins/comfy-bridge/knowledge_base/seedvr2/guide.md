# SeedVR2 — the agent's guide

ByteDance Seed's one-step diffusion restorer/upscaler for stills and video, Apache 2.0, free, native in
ComfyUI since v0.28.0 (the pinned 0.35.0 image has every node). It is an **enhancement stage**: it takes
another stage's image or video (or an upload) through `@image` / `@video` and returns it larger and cleaner.
It takes **no prompt**. Everything below was read from the official templates, node source and model cards
on 2026-10-01; `profile.json` holds the exact files, numbers and sources.

## When to pick it, and which recipe

- **Any video upscale/restore where the frames must stay temporally coherent** — this is the default over
  per-frame GAN upscalers (which shimmer). Also the default for restoring compressed or degraded stills.
- **Stills:** `upscale-image-3b` (official 3B Int8 template, 3.46 GB, the small-card choice),
  `upscale-image-7b` (official 7B Int8 template, 8.33 GB, "higher quality"), `upscale-image-7b-sharp`
  (the "enhanced detail" finetune; no official template, same graph, file swapped). Default scale ×4.
- **Video:** `upscale-video-3b` (official template, whole clip as one latent) and `upscale-video-3b-chunked`
  (the template's split-latent branch: Split SeedVR2 Latent → one KSampler pass per chunk → Merge). Use the
  chunked one for anything beyond a few seconds or when the VRAM law below says the clip will not fit.
  `upscale-video-7b` / `-7b-chunked` are the same graph with the 7B file (no official 7B video template;
  7B VRAM: NOT FOUND, community puts fp8-class 7B at 12–16 GB). Default scale ×2.
- Every recipe's `unet_name` port swaps precision inside a size: fp16 / fp8_e4m3fn / int8_convrot (template) /
  mxfp8 / nvfp4 files of the Comfy-Org repack. Pick precision by file; keep `weight_dtype` default.
- **Not this family** for: a clean 720p AIGC render that only needs to be bigger (it oversharpens — official
  limitation; use a GAN upscaler or lower the multiplier), heavy degradation or very large motion (may not
  clean up), GGUF / 8 GB cards (that is the numz pack, see `community_alternatives`), anything prompt-driven.

Stage composition: Wan/LTX/Hunyuan clip at 480p–720p → `upscale-video-*-chunked` ×2 → 1080p–1440p.
FLUX/Qwen still → `upscale-image-7b` ×4 for print.

## What the recipes are (one line each)

| recipe | needs | does | VRAM/time |
|---|---|---|---|
| `upscale-image-3b` | one image | ×4 restore + upscale, 1 step | 3B: the official law gives ~41 frames of 1080p per 24 GiB — a still is trivial |
| `upscale-image-7b` / `-7b-sharp` | one image | same, higher quality / crisper detail | 7B number NOT FOUND; 8.33 GB file |
| `upscale-video-3b` | one clip | ×2 restore of the whole clip in one latent, fps/audio/bit depth preserved | fits while latent frames × output Mpx ≤ (free GiB − 10.7) / 0.55 |
| `upscale-video-3b-chunked` | one clip | same, chunked to free VRAM automatically (auto mode applies the law), Hann-crossfaded merge | long clips, OOM route |
| `upscale-video-7b` / `-7b-chunked` | one clip | 7B quality on the video graph | NOT FOUND |

## Prompting

None. `SeedVR2Conditioning` builds positive/negative from embeddings baked into the checkpoint; a
`CLIPTextEncode` in the graph does nothing. The only "creative" controls are the scale multiplier, the seed
(micro-detail) and `color_correction_method` (none in the templates; `lab` = most faithful colour match to the
source, most memory).

## Settings that matter (the validator enforces the hard ones)

- **One step, cfg 1, denoise 1.** The templates fix steps and cfg; anything else is undocumented.
- **VAE:** `seedvr2_ema_vae_fp16.safetensors` (== `ema_vae_fp16.safetensors`), loaded with the core VAELoader;
  tiled encode/decode 512/128, temporal 64/8 for video, 4096 for stills.
- **Pre/post nodes are mandatory:** Preprocess pads H/W to ×16 and frames to 4n+1 (drops alpha); PostProcessing
  crops back and re-applies alpha from the *resized* original — wire the same resized tensor to both.
- **Scale** is applied before the DiT (the model restores at the size it is handed): ×4 stills, ×2 video.
  VRAM and time scale with output megapixels × frames.
- **VRAM law (official, 3B fp16):** `max_latent_frames = (free_GiB − 8.5 − 2.2) / (0.55 × output_Mpx)`;
  latent frames = (pixel frames − 1)/4 + 1. 24 GiB free → ~11 latent / ~41 pixel frames at 1080p, ~17 frames
  at 4K. Beyond that: the chunked recipe, a smaller scale, or trim (template tip).
- **Chunking:** Split's `latents` list feeds both Conditioning and the sampler; Merge's `temporal_overlap` is
  wired from Split's output. Manual `frames_per_chunk` must be 4n+1 (default 21). Overlap 0 = hard cuts.
- **fps/audio/bit depth** are linked through from GetVideoComponents — never a literal fps.

## When it goes wrong

| symptom | fix |
|---|---|
| black / empty output | official: disable FlashAttention, switch to FP16/FP8, re-download, disable live preview |
| OOM on a clip | `-chunked` recipe; lower scale; trim into segments |
| oversharpened / plastic texture | source was already clean: lower the multiplier, use 3B/7B not sharp, or a GAN upscaler |
| alpha lost / odd crop | PostProcessing.original_resized_images must be the resized RGBA, not raw LoadImage |
| model not found | native = Comfy-Org names in models/diffusion_models + models/vae; `seedvr2_ema_*` are numz files |
| need GGUF / BlockSwap / <8 GB | numz pack: GGUF Q4_K_M + BlockSwap + tiling; batch_size 4n+1, never below 5 |

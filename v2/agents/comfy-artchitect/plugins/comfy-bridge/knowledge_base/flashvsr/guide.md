# FlashVSR — the agent's guide

OpenImagingLab's streaming 4× video super-resolution (a DMD-distilled Wan 2.1 1.3B DiT with locality-constrained
sparse attention and a tiny conditional decoder), Apache 2.0 weights, free. **No core nodes**: every local
recipe needs a community pack, and Phase 2 must install it before the graph can run. It is an **enhancement
stage** for video only: another stage's clip comes in through `@video`, a 4× clip comes out. It takes no prompt.
Read from the official repo, the v1.1 model card and the packs' sources on 2026-10-01; `profile.json` holds the
exact files, numbers and sources.

## When to pick it, and when not

- **Pick it** when the user wants fast, temporally coherent 4× video upscaling and accepts a custom-node install:
  ~17 FPS at 768×1408 on one A100 with the reference code, "up to ~12× speedup over prior one-step diffusion VSR".
- **Prefer SeedVR2** (native, no pack, restores stills too) when a pack install is unwelcome, when the source is a
  still, or when the clip is short (< 21 frames). Prefer the GAN upscalers when there is no VRAM headroom at all.
- **Not this family** for: 2× or 3× as the goal (4× is the trained setting — "strongly recommend"; other scales
  "may show reduced stability": upscale 4× and resize down), stills, prompts, guaranteed acceleration on RTX 40/50
  with the official Block-Sparse backend (verified only on A100/A800; the wrappers substitute Sparse_Sage).

Stage composition: Wan/LTX clip at 480p → `upscale-video-4x` → 1920p-class; then a resize node if the
deliverable is 1080p.

## What the recipes are (one line each)

| recipe | pack | does | VRAM |
|---|---|---|---|
| `upscale-video-4x` | LacklusterOpsec/ComfyUI-Lackluster-FlashVSR (MIT, registry ComfyUI-FlashVSR_Stable) | v1.1, full mode (Wan 2.1 VAE decode), bf16, Sparse_Sage, no tiling/chunking | the pack's 24 GB+ row |
| `upscale-video-4x-16gb` | same pack | tiny mode (TCDecoder), tiled VAE+DiT, unload_dit, 50-frame chunks; ports carry the 12 GB (fp16, LightVAE) and 8 GB (tiny-long, LightTAE, 16–32-frame chunks) rows | 16 GB row |
| `upscale-video-4x-ultrafast` | lihaoyun6/ComfyUI-FlashVSR_Ultra_Fast (GPL-3.0, the original) | node defaults at scale 4: tiny, tiled, bf16; no chunking, no alt-VAE catalogue | NOT FOUND |

Install exactly one of the two packs: they register the same class ids (FlashVSRNode / FlashVSRNodeAdv /
FlashVSRInitPipe). 1038lab/ComfyUI-FlashVSR (presets, independent ids, own repack) is documented under
`community_alternatives` only. All recipes carry the validator's "unknown class" finding by design until the pack
is installed and `/object_info` is re-read; input names come from the packs' `nodes.py`, confirm them then.

Files: the four v1.1 files (DiT 5.68 GB fp32-stored, LQ_proj_in, TCDecoder, Wan2.1_VAE) in `ComfyUI/models/FlashVSR/`.

## Prompting

None. A fixed positive embedding ships with the pack (`posi_prompt.pth`).

## Settings that matter (the validator enforces the hard ones)

- **≥ 21 input frames** (streaming warm-up; short inputs fail or duplicate frames). Never a still.
- **scale 4**; 2 is only a VRAM concession.
- **mode:** tiny (fast, TCDecoder) / tiny-long (long clips, low VRAM) / full (Wan VAE decode, best). `alt_vae`
  only applies in full mode.
- **attention_mode:** a *sparse* backend — `sparse_sage_attention` on consumer cards (sm_75–sm_120);
  `block_sparse_attention` is the official one but A100/A800-only in practice; `sdpa` / `flash_attention_2` drop
  the sparse attention and degrade quality at high resolution (official warning).
- **VRAM ladder (Lackluster README):** 24 GB+ full, no tiling; 16 GB tiny + tiling + chunks 50–100, bf16;
  12 GB tiny + LightVAE_W2.1 + chunks 50, fp16; 8 GB tiny-long + LightTAE_HY1.5 + chunks 16–32, fp16.
  Progressive fallback on OOM: tiled VAE → tiled DiT → chunking; `unload_dit` cuts the decode peak;
  `fp8_e4m3fn` halves VRAM on Ada/Hopper only.
- **Sparse knobs:** sparse_ratio 1.5 (faster)–2.0 (stable); kv_ratio 1.0 (less VRAM)–3.0 (quality);
  local_range 9 (sharper) / 11 (stable). Tiles 32–1024 step 32, overlap 8–512 step 8 and < tile/2.
- **color_fix on** (wavelet colour transfer). **fps** wired through from GetVideoComponents.
- Use the **v1.1** weights (aspect-ratio artifact fix, "enhanced stability + fidelity").

## When it goes wrong

| symptom | fix |
|---|---|
| soft output at high resolution | a dense attention backend — switch to sparse_sage_attention |
| Triton / attention build error on Turing or older | `pip install -U "triton<3.3.0"` (Windows: triton-windows<3.3.0) |
| OOM | tiled VAE → tiled DiT → frame_chunk_size / tiny-long; unload_dit; LightVAE; fp8 on Ada/Hopper |
| fails or duplicates frames | fewer than 21 frames in |
| "streaming pipeline produced no output frames" | update the Lackluster fork (fixed in 1.5.1) |
| nodes missing / doubled after install | both packs installed — keep one |
| colour drift | color_fix True |

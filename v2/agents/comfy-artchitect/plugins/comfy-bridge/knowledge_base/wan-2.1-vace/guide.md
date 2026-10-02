# video-inpaint additions: SAM3 + Wan 2.1 VACE object replacement

## Proposed family: `wan-2.1-vace`

The recipe `object-replace-sam3-vace-14b` runs **Wan 2.1 VACE 14B** (`wan2.1_vace_14B_fp16.safetensors`). It does not use any Wan 2.2 weight, so it does not belong in the live `wan-2.2` profile. That profile's own `vace-14b` recipe uses the Wan 2.2 VACE-Fun pair.

Propose a new family profile `wan-2.1-vace`:

- `kind: ["video"]`, `tasks: ["video-inpaint", "vace"]`, `cost: free`, license Apache-2.0 for the VACE weights (Comfy-Org repack and Wan-AI/Wan2.1-VACE-14B cards).
- First recipe: `object-replace-sam3-vace-14b` (this folder).
- Natural later additions: the native Wan 2.1 VACE templates `video_wan_vace_14B_v2v` / `ref2v`, which the live profile already cites as S-TPL-VACE-*.

One alternative was considered: a task-centred profile named `video-inpaint`, holding several model families. It was rejected because the KB is organised one profile per model family, and the `rules` (VAE / text-encoder pairing, frame arithmetic) are family facts.

The segmenter (SAM3) is a preprocessing pack. It is not a model family, so it lives in the recipe's `packs` and in `files.json` only.

## What is pending

- **Packs, all Phase 2 installs:**
  - ComfyUI-WanVideoWrapper (main)
  - **ComfyUI-SAM3, pinned at commit `978bb763`.** Main renamed `SAM3Propagate.sam3_model` to `sam3_model_config` and dropped `LoadSAM3Model.model_path`. The required changes are in the recipe's `packs[1].status`.
  - ComfyUI-KJNodes
  - ComfyUI_essentials
- **VRAM:** NOT FOUND. The template is distributed for cloud only and loads the 34.7 GB fp16 model unquantised.
- **min_comfyui 0.20.0** (template), which is below the pinned 0.35.0.

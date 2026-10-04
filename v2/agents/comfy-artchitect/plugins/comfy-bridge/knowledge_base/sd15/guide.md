# Stable Diffusion 1.5 — the agent's guide

The 2022 512-pixel model behind a very large share of Civitai's images and fine-tunes. It is in the
knowledge base to RECREATE those images faithfully (reference_recipe), not to be picked for new work:
newer families beat it on prompt adherence, text and resolution.

## Which recipe

- `t2i-base` — ComfyUI's own default workflow: 20 steps euler/normal, cfg 8, 512x512, negative
  `text, watermark`. The `checkpoint` port takes any SD 1.5 fine-tune (DreamShaper, majicMIX, …: Comfy
  Cloud has several; a Civitai one comes in the stage's `models` with base `SD 1.5`); `clip_skip` -2
  when the record says "Clip skip: 2".

## Prompting

Short comma-separated phrases, as the default template writes them. A fine-tune's own card sets its
tags and negative; a Civitai record's prompt and negative are used as they are.

## Limits

Trained at 512x512: much larger canvases repeat subjects — render near 512 (fine-tunes ~768) and
upscale after. No legible text.

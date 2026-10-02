# SDXL 1.0 and its ecosystem — the agent's guide

Stability AI's 2023 1-megapixel image model: **base** (optionally + **refiner** for the last 20% of
steps), the real-time **SDXL-Turbo**, and the largest third-party ecosystem of any image family —
ByteDance **Lightning** and **Hyper-SD**, **DMD2**, **LCM-LoRA**, xinsir **ControlNet Union**, IP-Adapter,
and the fine-tune lineages (Juggernaut, RealVisXL, Illustrious, Pony). Free weights; **the license is a
spread**: base/refiner/Lightning/LCM are openrail++, Turbo is the Stability Community License (<US$1M,
register), **DMD2 is non-commercial**, Juggernaut forbids paid-API use without a license, Pony and
Illustrious are FAIPL-1.0-SD (derivatives stay open). Read from the official templates and publisher
cards on 2026-10-01; `profile.json` holds files, numbers and sources.

## When to pick it, and when not

- **Pick SDXL** for a style the ecosystem covers (photoreal via Juggernaut/RealVisXL, anime via
  Illustrious/Pony), for a ControlNet- or LoRA-heavy pipeline, for a cheap fast draft (Turbo / Lightning),
  or when a fine-tune the user already owns is SDXL.
- **Not SDXL** for text in the image ("The model cannot render legible text"), for compositional
  instructions ("a red cube on top of a blue sphere"), or when raw prompt adherence matters more than
  the ecosystem — SD3.5 / Qwen-Image / Z-Image do those better.

## Which recipe

- **Plain still:** `t2i-base` — 25 steps, dpmpp_2m/karras, cfg 7, 1024². Its `checkpoint` port is where
  a fine-tune goes; follow that fine-tune's prompting rules below.
- **Best quality from the Stability weights:** `t2i-base-refiner` (shared prompt) or
  `t2i-base-refiner-split-prompt` (refiner prompt = the *look*, not the scene). Two `KSamplerAdvanced`,
  base 0→20 of 25 with leftover noise, refiner 20→end without adding noise, refiner CLIP and VAE.
- **Fast:** `t2i-turbo` (1 step, 512², cfg 1, `SDTurboScheduler` + `SamplerCustom`),
  `t2i-lightning-4step` / `-8step` (euler + sgm_uniform, cfg 1, 1024px, openrail++),
  `t2i-lightning-4step-lora` (the LoRA on a fine-tune — Lightning says use the LoRA only on non-base models),
  `t2i-dmd2-4step` (lcm sampler, cfg 1 — **non-commercial**).
- **From an image:** `img2img-base` (denoise 0.87), `inpaint-base` (alpha mask → `VAEEncodeForInpaint`).
- **Structure:** `control-union-canny` (core Canny in-graph, no pack) or `control-union` with a map you
  preprocessed upstream (openpose / depth / lineart / scribble / segment / normal / tile / repaint).
- **Concept from a reference image:** `revision` / `revision-2ref` (CLIP-vision G → `unCLIPConditioning`).
  Concepts, not identity — identity needs IP-Adapter (pack; pending).

| recipe | needs | does |
|---|---|---|
| `t2i-base` | prompt | 1024² still; checkpoint port for fine-tunes |
| `t2i-base-refiner` / `-split-prompt` | prompt | base + refiner 80/20 |
| `t2i-turbo` | prompt | 1 step at 512² |
| `t2i-lightning-4step` / `-8step` | prompt | 4 / 8 steps, cfg 1 |
| `t2i-lightning-4step-lora` | prompt + a fine-tune | 4 steps on any SDXL checkpoint |
| `t2i-dmd2-4step` | prompt | 4 steps, lcm; NC license |
| `img2img-base` | image | denoise 0.87 repaint; output = input size |
| `inpaint-base` | RGBA image (alpha = mask) | repaint inside the mask |
| `control-union` | preprocessed map | Union ProMax, type `auto` |
| `control-union-canny` | any image | in-graph Canny, type `canny/lineart/anime_lineart/mlsd` |
| `revision` / `revision-2ref` | 1 / 2 reference images | concept transfer |

Disk: each checkpoint ~6–7 GB, Union 2.5 GB, clip_vision_g 3.7 GB. No official VRAM figures exist.
Byte-exact sizes are pending (the KB holds decimal GB only).

## Prompting

**Negative default:** `text, watermark` (every ComfyUI_examples SDXL template). Turbo, Lightning and
DMD2 run at cfg 1, where the negative is skipped. Fine-tunes differ: Juggernaut says start with *none*;
RealVisXL ships `bad hands, bad anatomy, ugly, deformed, (face asymmetry, eyes asymmetry, deformed eyes,
deformed mouth, open mouth)`; Pony says negatives are "Generally not needed".

**Positive:** short tag strings ("evening sunset scenery blue sky nature, glass bottle with a galaxy in
it") and prose both work. Refiner prompts "should emphasize desired visual effects rather than scene
content". **Pony:** prefix `score_9, score_8_up, score_7_up, score_6_up, score_5_up, score_4_up`, then
the description and tags; `source_anime` / `source_pony` / `rating_safe` etc.; **and `CLIPSetLastLayer -2`
or you get "low quality blobs"**. **Illustrious:** Danbooru tags with quality tags (`masterpiece, best
quality`), composition tags (`upper body`, `cowboy shot`), don't stack conflicting composition tags.
**Juggernaut:** 832×1216 / 1216×832, DPM++ 2M Karras, 30–40 steps, cfg 3–7 (lower = more realistic).
**RealVisXL:** DPM++ SDE Karras 30+ or 2M Karras 50+, hires-fix denoise 0.1–0.3.

What hurts: text, compositional instructions, negatives/cfg>1 on distilled models, Pony without clip skip
and score tags, Illustrious without quality tags.

## Settings that matter (the validator enforces the hard ones)

- **Latent:** `EmptyLatentImage` (4-channel), multiples of 8; **trained buckets** 1024×1024, 1152×896,
  896×1152, 1216×832, 832×1216, 1344×768, 768×1344, 1536×640, 640×1536. Turbo: 512×512.
- **Refiner hand-off:** base `add_noise enable` + `return_with_leftover_noise enable` + `end_at_step` =
  refiner `start_at_step`; refiner `add_noise disable`; same total steps; refiner CLIP for its prompts,
  refiner VAE for the decode.
- **Distilled:** cfg **1**; Lightning steps = the file's N, euler + sgm_uniform; DMD2 4 steps + lcm;
  Hyper-SD CFG LoRAs are the exception (cfg 5–8 at 8/12 steps).
- **Union ControlNet:** `ControlNetLoader → SetUnionControlNetType → ControlNetApplyAdvanced`; on this box
  the type keys are `auto, openpose, depth, hed/pidi/scribble/ted, canny/lineart/anime_lineart/mlsd,
  normal, segment, tile, repaint` — not the newer short names.
- **VAE:** the baked fp16 VAE can NaN → black images; the fix is madebyollin's `sdxl_vae.safetensors`
  in a `VAELoader`. The templates don't carry it; the validator warns, answer it if the output is fine.
- **cfg:** 7–8 on base; template note: max 10, higher "burn-in".

## When it goes wrong

| symptom | fix |
|---|---|
| black / grey image | fp16-fix VAE (madebyollin) into VAEDecode |
| refiner output noisy or blurry | restore the hand-off flags; same steps on both; refiner CLIP + VAE |
| mushy / duplicated subjects | a trained bucket |
| fried colours on Turbo/Lightning/DMD2 | cfg 1, right step count, right sampler |
| Pony blobs | `CLIPSetLastLayer -2` + score prefix |
| ControlNet does nothing | the map must be preprocessed (Canny in core; others need comfyui_controlnet_aux) |
| union type rejected | use the v0.35.0 grouped keys or `auto` |
| two ControlNet files overwrite each other | rename `diffusion_pytorch_model*.safetensors` on download |
| Hyper-SD 1-step inert | needs ComfyUI-TCD / the repo's scheduler node (pending) |
| "can I sell this?" | not with DMD2; Turbo needs Stability registration (<US$1M); Juggernaut no paid API; Pony needs permission |

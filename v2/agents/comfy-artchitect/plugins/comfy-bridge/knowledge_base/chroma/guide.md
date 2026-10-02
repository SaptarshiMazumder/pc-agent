# Chroma — the agent's guide

Lodestone Rock's 8.9B text-to-image model rebuilt from FLUX.1-schnell, Apache 2.0, free, deliberately
unaligned ("fully uncensored, reintroducing missing anatomical concepts"). A **full-CFG** model: unlike
Flux dev/schnell the negative prompt works. Text encoder is **T5-XXL only** — there is no CLIP-L.
Everything below was read from the official templates, model cards and ComfyUI source on 2026-10-01;
`profile.json` holds the exact files, numbers and sources.

## When to pick it, and which recipe

- **Flux-class photographic / artistic / anime output with a working negative prompt, or an
  uncensored base:** `t2i-hd` — Chroma1-HD fp8mixed (9.2 GB) + T5 fp8 (5.2 GB), 26 steps beta, cfg 3.5,
  euler, shift 1. VRAM floor NOT FOUND (no official statement).
- **Full precision:** `t2i-hd-bf16` — the 17.8 GB file; pair with `t5xxl_fp16` when memory allows
  ("fp16 is recommended").
- **Fast:** `t2i-flash` — Chroma1-Flash, "the fast CFG baked version": heun / beta, 8 steps, cfg 1.
  **Community settings** — the Flash README is empty; marked community-confirmed.
- **Experimental:** `t2i-radiance` — pixel-space Chroma1-Radiance (19 GB, no VAE at all). Officially WIP:
  "expect some squiggles on the details part of the image". Pin the Comfy-Org snapshot.
- **Not this family** for editing, inpainting, ControlNet, IP-Adapter or img2img — none exist (NOT FOUND).
  Use Chroma for the T2I pass and another family for those stages.

## What the recipes are (one line each)

| recipe | needs | does |
|---|---|---|
| `t2i-hd` | prompt + negative | the official template: CFGGuider 3.5, BasicScheduler beta 26, euler, SamplerCustomAdvanced |
| `t2i-hd-bf16` | prompt + negative | same graph, bf16 file |
| `t2i-flash` | prompt | heun / beta, 8 steps, cfg 1 (community) |
| `t2i-radiance` | prompt + negative | ChromaRadianceOptions + EmptyChromaRadianceLatentImage + VAELoader `pixel_space`, BetaSamplingScheduler 30 (0.4/0.4) |

Disk: HD fp8mixed + T5 fp8 + VAE ≈ 14.7 GB; bf16 HD + T5 fp16 ≈ 27.9 GB; Radiance 19 GB + T5.

## Prompting

**Negative prompt — the template's, use it as is (it is effective):**

This low quality greyscale unfinished sketch is inaccurate and flawed. The image is very blurred and lacks detail with excessive chromatic aberrations and artifacts. The image is overly saturated with excessive bloom. It has a toony aesthetic with bold outlines and flat colors.

(Card alternative: `low quality, ugly, unfinished, out of focus, deformed, disfigure, blurry, smudged,
restricted palette, flat colors`. Flash ignores the negative at cfg 1.)

**Positive prompt:** one descriptive paragraph — the encoder is T5, so sentences beat tags. Official
examples end with photographic qualifiers: *"Amateur photography. Unfiltered. Real life. Natural light.
Subtle shadows."* Card example: *"A high-fashion close-up portrait of a blonde woman in clear sunglasses.
The image uses a bold teal and red color split for dramatic lighting. The background is a simple
teal-green. The photo is sharp and well-composed, and is designed for viewing with anaglyph 3D glasses for
optimal effect. It looks professionally done."* Text rendering is demonstrated with a quoted title
(`"CHROMA1-HD"` in large white 3D letters); how far it goes is NOT FOUND.

What hurts: tag soup; very high cfg with shift 1 (community ceiling 6–7); Flash above cfg ~1.5 or with
many steps.

## Settings that matter (the validator enforces the hard ones)

- **Text encoder:** `CLIPLoader` type **`chroma`** with a `t5xxl_*` file. **`DualCLIPLoader` type flux with
  clip_l is WRONG** — Chroma has no CLIP.
- **VAE:** HD / Base / Flash → FLUX `ae.safetensors`. **Radiance → `VAELoader` "pixel_space"**, no file.
- **shift:** `ModelSamplingAuraFlow` **1.0** ("intended shift by the creator ... greater detail"); up to 3.0
  only for complex compositions. The node default 1.73 is not Chroma's value.
- **cfg:** 3.5 (template), 3.8 (lodestones), 3.0 (card); community up to 6–7. Flash: 1.
- **steps / scheduler:** 26 beta (template), 30 BetaSamplingScheduler 0.4/0.4 (Radiance), 40 (card). Flash 8 heun.
- **Sizes:** multiples of 16; 1024² (templates), 1152² (lodestones).
- **T5TokenizerOptions:** min_padding 0 / min_length 0 ("min_padding 1 is the official way. setting it to 0 works too.").
- **Radiance:** `nerf_tile_size` -1 (=32); raise for speed until OOM; 0 = no tiling (lots of VRAM).

## When it goes wrong

| symptom | fix |
|---|---|
| garbled prompt following / clip_l load error | CLIPLoader type chroma + T5 only, not DualCLIPLoader |
| less detail than the template | shift 1.0 |
| old guide says ComfyUI_FluxMod / chroma-unlocked-vNN | deprecated; Chroma1-HD + core nodes |
| squiggles in fine detail (Radiance) | WIP model; pin the Comfy-Org snapshot |
| Radiance slow / OOM | tune nerf_tile_size |
| Flash looks like a slow HD run | heun / beta, 8 steps, cfg 1 |
| lodestones' fp8_scaled_rev2 file missing | Comfy-Org Chroma1-HD-fp8mixed |
| wanting ControlNet / inpaint | not on Chroma; another family |

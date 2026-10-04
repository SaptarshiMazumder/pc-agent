# Stable Diffusion 3.5 — the agent's guide

Stability AI's 2024 MMDiT image family: **Large** (8B, 1 MP, the quality model), **Large Turbo** (4-step distillation), **Medium** (2.5B, 0.25–2 MP, consumer cards) and three **Large-only ControlNets** (canny, depth, blur). Everything below was read from the official Comfy-Org templates, Stability's reference code and the Comfy-Org repack on 2026-10-01; `profile.json` holds files, numbers, sources.

## When to pick it, and which recipe

- **Text-to-image with strong prompt adherence and typography, no gating hassle:** `t2i-large` — the
  ungated Comfy-Org fp8 all-in-one (encoders inside), one checkpoint loader, 20 steps, cfg 4.01, 1024².
- **Maximum fidelity, user has an HF account:** `t2i-large-triple-clip` — the original fp16 weights plus `TripleCLIPLoader(clip_l, clip_g, t5xxl)`. The original checkpoints carry **no text encoders**; the all-in-one is the only file that does. The originals are **gated**.
- **Speed:** `t2i-large-turbo` — 4 steps, cfg 1.2, nothing else changes. Gated like the original; the
  ungated route is city96's GGUF (needs the ComfyUI-GGUF pack, pending).
- **Small card / fast drafts:** `t2i-medium`; for people, hands and busy compositions `t2i-medium-slg`
  (Skip Layer Guidance layers 7,8,9 — Stability's own recommendation — at cfg 4.0).
- **Structure from an image:** `control-canny` (edges computed in-graph), `control-depth` (Lotus depth
  estimated in-graph; output takes the input's aspect at ~1 MP), `control-blur` (re-render / upscale
  from a **pre-blurred** image — the graph does not blur). All three need the **Large** checkpoint.
- **Not this family** for: img2img / inpaint with a validated graph (no SD3.5 template — compose
  `VAEEncode` into `t2i-large` if you must), image prompting (no IP-Adapter), ControlNet on Medium,
  negative-prompt-driven styling, more than ~1 MP on Large.

Stage composition: SD3.5 is the still-image stage before a video family (Wan 2.2 `i2v-14b`) or an
upscaler; `control-blur` is itself a refinement stage when a blur is applied upstream.

## What the recipes are (one line each)

| recipe | needs | does |
|---|---|---|
| `t2i-large` | prompt | 1024² still, 20 steps, the official template |
| `t2i-large-triple-clip` | prompt; gated download | fp16 original + separate encoders, 30 steps, cfg 5.45 |
| `t2i-large-turbo` | prompt; gated download | 4 steps, cfg 1.2 (official guidance, no template graph) |
| `t2i-medium` / `-slg` | prompt | 2.5B model, 0.25–2 MP; SLG variant for anatomy at cfg 4.0 |
| `control-canny` | any image | in-graph Canny (0.3/0.6) at 1024², strength 0.66, 32 steps |
| `control-depth` | any image | in-graph Lotus depth, strength 0.7, cfg 8, size = depth map (megapixels port) |
| `control-blur` | a pre-blurred image | strength 1.0, 30 steps, cfg 4; EmptySD3LatentImage sets the size |

Disk: the fp8 all-in-one is 14.9 GB; each ControlNet 8.7 GB; the fp16 original 16.5 GB + 6.8 GB of
encoders (fp8 T5). No official VRAM figures exist for any SD3.5 file. Byte-exact sizes are pending
(the KB holds decimal GB only).

## Prompting

**Negative prompt — the official default is the empty string.** Every SD3.5 template ships `""`, and the
ControlNet templates use `ConditioningZeroOut(positive)` as the negative. SD3/3.5 negatives behave
opposite to SDXL: "blurry, distorted, ugly" lists *hurt* quality and cause colour banding
(community-confirmed). If a negative is really needed, time-limit it: real negative for 0–10% of the
schedule (`ConditioningSetTimestepRange`), zeroed conditioning after, joined with `ConditioningCombine`.

**Positive prompt:** one dense natural-language description. T5 reads long prompts (256 tokens), the
CLIPs 77. The templates read like "a bottle with a pink and red galaxy inside it on top of a wooden table
… bright sun clouds forest"; Stability's reference prompts are photographic sentences ("A Night time
photo taken by Leica M11, portrait of a Japanese woman in a kimono, looking at the camera, Cherry
blossoms"). For text in the image, quote the string. `CLIPTextEncodeSD3` can route tags to CLIP and
prose to T5; the official templates do not bother.

What hurts: heavy negatives; cfg well above 5 on Large (reference 4.5); SLG without lowering cfg;
a ControlNet on Medium.

## Settings that matter (the validator enforces the hard ones)

- **Latent:** `EmptySD3LatentImage` (16-channel), width/height multiples of 16. Never `EmptyLatentImage`.
- **shift:** 3.0 — the SD3 config value *and* the `ModelSamplingSD3` default, so the templates omit the
  node. Only add it to move away from 3.0 on purpose.
- **cfg:** Large 4–5.45 (reference 4.5); Medium 5.0, **4.0 with SLG**; Turbo **1.0–1.2**; ControlNets
  reference 3.5, templates 4–4.5 (depth 8).
- **steps:** 20–30 (templates), 40 (Large reference), 50 (Medium reference), 50–60 for ControlNets
  (card, "especially with Canny"), **4 for Turbo**.
- **Sampler:** euler + sgm_uniform (templates); dpmpp_2m in the reference code; euler for Turbo/ControlNets.
- **Encoders:** `TripleCLIPLoader` order is clip_l, clip_g, t5xxl. T5 fp8-scaled by default; fp16 only
  with >32 GB system RAM.
- **ControlNets:** Large only; `ControlNetApplyAdvanced` **with the vae input wired**; `ControlNetApplySD3`
  is deprecated. Strength 0.66–1.0 (card: start at 0.7–0.8).

## When it goes wrong

| symptom | fix |
|---|---|
| "Access to model … is restricted" | use the Comfy-Org repack (ungated) — all-in-one fp8, text_encoders, repackaged ControlNets |
| empty conditioning / prompt ignored with an original checkpoint | the file has no encoders: `TripleCLIPLoader` |
| ControlNet garbage on Medium | ControlNets are Large-only |
| deprecated "Apply Controlnet with VAE" | `ControlNetApplyAdvanced` + vae |
| depth output ignores width/height | size comes from the depth map — use `megapixels`, or wire `EmptySD3LatentImage` |
| Turbo looks fried / slow | 4 steps, cfg 1.0–1.2 |
| Medium anatomy | `t2i-medium-slg`, cfg 4.0 |
| colour banding | empty the negative |
| T5 eats the RAM | fp8-scaled T5 |
| blur recipe repaints instead of refining | the input must be pre-blurred (GaussianBlur kernel 50) |

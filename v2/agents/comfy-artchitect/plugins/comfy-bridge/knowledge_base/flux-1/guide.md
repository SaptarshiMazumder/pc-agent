# FLUX.1 — the agent's guide

Black Forest Labs' 12B rectified-flow image family (2024-08), free open weights. The default pick for a high-quality still from text, for instruction editing (Kontext), for inpaint/outpaint (Fill), for edge/depth-guided generation (Canny/Depth) and for image-style reference (Redux). Everything below was read from the official Comfy-Org templates, docs and BFL cards on 2026-10-01; `profile.json` holds the exact files, numbers and sources.

## When to pick it, and which recipe

- **Quality still, 24 GB card:** `t2i-dev` (bf16 dev, 20 steps). Best prompt following and quality in
  the family; needs the 9.8 GB `t5xxl_fp16` only with >32 GB system RAM — swap to `t5xxl_fp8_e4m3fn`.
- **Under 24 GB, one file:** `t2i-dev-fp8` (17 GB all-in-one checkpoint, "quality slightly lower").
- **Fast:** `t2i-schnell` / `t2i-schnell-fp8` or `t2i-dev-turbo` (dev + Turbo-Alpha LoRA, 8 steps, community-confirmed, dev only).
- **Photoreal people/scenes:** `t2i-krea-dev` — the Krea finetune was trained against the oversaturated
  "AI look"; drop-in for dev, 11.9 GB fp8_scaled.
- **Edit an existing image by instruction:** `kontext-edit` (one image), `kontext-edit-multi` (two images
  stitched, output = stitched canvas) or `kontext-edit-multi-ref` (one reference per image, chosen output
  size; community-confirmed assembly of the template's own note). English prompts only.
- **Fill a masked region / extend the canvas:** `fill-inpaint` (mask in the image's alpha, MaskEditor) /
  `fill-outpaint` (pad amounts per side). Guidance 30.
- **Follow a structure:** `canny-lora-dev` or `depth-lora-dev` (1.2 GB LoRAs on flux1-dev — prefer these
  when dev is already on disk), `canny-dev` (the 23.8 GB full Canny model), `depth-lora-dev-lotus` (the
  depth map estimated in-graph from an RGB image).
- **"Make it look like this image":** `redux-dev` / `redux-dev-multi` (style/variation from 1-2 references;
  a prompt is optional).
- **Not this family** for: negative prompts or real CFG (none — cfg is always 1), non-English Kontext prompts, FLUX.2-class text rendering.

Stage composition the user usually wants: `t2i-dev` → `kontext-edit` for a tweak; a depth/canny map from
a reference → `depth-lora-dev` / `canny-lora-dev`; a still → Wan/LTX for motion.

## What the recipes are (one line each)

| recipe | needs | does |
|---|---|---|
| `t2i-dev` / `t2i-dev-fp8` | prompt | 1024² still; UNET+DualCLIP+VAE path vs one fp8 checkpoint |
| `t2i-dev-turbo` | prompt | dev at 8 steps with the Turbo-Alpha LoRA |
| `t2i-krea-dev` | prompt | photographic finetune, same graph as dev |
| `kontext-edit` / `-multi` / `-multi-ref` | 1-2 images | instruction edit keeping identity/style |
| `fill-inpaint` / `fill-outpaint` | image (+ alpha mask) / image + pad sizes | Fill model, guidance 30 |
| `canny-dev`, `canny-lora-dev` | image | edge-guided generation (Canny computed in-graph) |
| `depth-lora-dev` / `-lotus` | depth map / RGB image | depth-guided generation |
| `redux-dev` / `-multi` | 1-2 style images | image variation / style transfer, prompt optional |

Disk: dev bf16 + clip_l + t5 fp16 + ae ≈ 34 GB; the fp8 checkpoint alone 17 GB; Kontext/Krea fp8_scaled
12 GB + 5.4 GB encoders. Pending: GGUF Q4-Q8 for 8-12 GB cards (needs the ComfyUI-GGUF pack).

## Prompting

**Negative prompt: none.** cfg is 1.0 everywhere, so the negative is ignored; the graphs feed
`ConditioningZeroOut` (or an empty string). Say what you want instead of what you don't: "no people" →
"empty, deserted"; "no text" → "clean surfaces, unmarked".

**Positive:** plain natural language, `[SUBJECT], [LOCATION], [STYLE], [CAMERA SETTINGS], [LIGHTING],
[COLORS], [EFFECT]`; 30-80 words is the sweet spot, "Start short. Add only what changes the image.
Specific detail helps. Filler hurts." T5 cuts at 512 tokens (~350 words). The schnell full graph has two
prompt boxes: T5 gets the sentence, CLIP-L gets a short keyword/style line.

**Kontext (editing):** "Change the car color to red"; "Change to Bauhaus style while maintaining the
original composition"; "Change the clothes to be a viking warrior while preserving facial features";
"Change the background to a beach while keeping the person in the exact same position, scale, and
pose"; text edits in quotes: "Replace 'joy' with 'BFL', maintain the same font style". Use change /
replace, not transform; name what must stay; break big edits into steps.

What hurts: negation words; vague verbs; SD tag soups and `(word:1.2)` weights; prompts beyond 512
tokens; non-English Kontext prompts; "Put him on a beach" without saying what stays.

## Settings that matter (the validator enforces the hard ones)

- **cfg 1.0, always.** Strength lives in **FluxGuidance**: dev default 3.5 (no node in the T2I template),
  Kontext 2.5, Fill 30, Canny 30, Depth LoRA 10, Redux 3.5; realism advice 2-3.5, "above 5 looks
  deep-fried". Schnell ignores guidance entirely.
- **Loaders:** `UNETLoader` + `DualCLIPLoader` type `flux` (one slot `clip_l`, one `t5xxl_*`) +
  `VAELoader ae.safetensors`; or `CheckpointLoaderSimple` for the two fp8 all-in-one files only.
- **Steps:** 20 (dev family), 4 (schnell), 8 (Turbo-Alpha). Sampler euler; scheduler simple (T2I,
  Kontext, Redux) or normal (Fill, Canny, Depth).
- **Latent:** `EmptySD3LatentImage`, multiples of 16, ~1 MP (1024², or the Kontext list 672×1568 …
  1568×672). Kontext output = the scaled input unless you give it an EmptySD3LatentImage.
- **Companion nodes are not optional:** Fill → `InpaintModelConditioning` (+ `DifferentialDiffusion`;
  noise_mask true inpaint / false outpaint); Canny/Depth (model or LoRA) → `InstructPixToPixConditioning`;
  Kontext → `FluxKontextImageScale` + `ReferenceLatent`; Redux → SigLIP `CLIPVisionLoader` +
  `StyleModelApply`.
- **LoRAs:** Depth/Canny LoRAs and Turbo-Alpha go on flux1-dev only; dev LoRAs transfer poorly to schnell.
- **Precision:** fp8 saves VRAM on every card but is faster only on RTX 40+; NVFP4 files need RTX 50.

## When it goes wrong

| symptom | fix |
|---|---|
| grey / washed-out image, or "expected ... 4 channels, but got 16" | `ae.safetensors` as VAE; cfg 1.0 |
| OOM loading the text encoder | `t5xxl_fp8_e4m3fn(_scaled)`; `DualCLIPLoader.device = cpu` |
| plastic skin, "deep-fried" | FluxGuidance 2-3.5; try Krea |
| Kontext changes identity / repaints everything | name what stays; keep FluxKontextImageScale; guidance ≈2-2.5; edit in steps |
| Fill seams / colour shift at the border | grow + blur the mask, keep feathering 24 and DifferentialDiffusion, composite the original back |
| Fill/Canny/Depth output is garbage | the conditioning node is missing (see above) |
| schnell ignores FluxGuidance | expected (guidance_embed off); use steps/seed/prompt |
| LoRA weak on schnell | use dev/Krea |
| fp8 no faster on a 30-series | expected; GGUF Q8_0 instead |
| NVFP4 file slow / fails to load | Blackwell only; use fp8_scaled |

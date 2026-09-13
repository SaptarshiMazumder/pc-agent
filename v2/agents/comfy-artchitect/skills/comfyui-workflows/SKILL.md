---
name: comfyui-workflows
description: Use when designing, emitting, running or repairing a ComfyUI workflow — how to research what a model family needs, the two JSON formats, and how to read what an instance rejected.
always: true
---

# Building a ComfyUI workflow that runs

## The two formats

**API format** — what `POST /prompt` accepts, and the only thing that runs:

```json
{
  "4": { "class_type": "CheckpointLoaderSimple", "inputs": { "ckpt_name": "sd_xl_base_1.0.safetensors" } },
  "3": { "class_type": "KSampler",
         "inputs": { "seed": 42, "steps": 20, "cfg": 8.0,
                     "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
                     "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0],
                     "latent_image": ["5", 0] } }
}
```

Keys are node ids **as strings**. An input is a literal, or a link written `[upstream_id, slot]`.
No positions, no link table, no version.

**UI format** — `nodes[]`, `links[]`, `widgets_values`. What the browser imports. Never
hand-write it and never hand-convert into it; `comfy_emit` produces both from one node list.

## Finding out what a model needs — before any graph

The graph is dictated by the model FAMILY, families wire completely differently (a
self-contained checkpoint vs a bare unet with separate text encoders and VAE; cfg 7 vs cfg 1;
20 steps vs 4), and new families ship monthly — so the wiring is **researched, never recalled**:

0. **Which model at all?** START FROM THE FIELD GUIDE AT THE END OF THIS FILE — the current
   floor per task, paid and open, dated. Then sweep BOTH halves of the landscape to confirm or
   beat it: `web_search` ("best <task> model <year>", "<task> comfyui workflow") and
   `comfy_research` on Hugging Face and Civitai for OPEN WEIGHTS, **and** the API-node side —
   `comfy_node_search` the providers (the REAL class names, with deprecated flags),
   `comfy_node_spec` the candidates, ComfyUI's partner-node docs, and the current hosted
   services (Seedance/ByteDance, Wan, Kling, Veo, MiniMax and their successors). HF and Civitai
   carry open weights only, so a sweep limited to them returns free candidates every time and
   calls that "the best available".
   - **Rank on fitness for the job, never on price.** The best model wins whether it is open
     weights or a paid API.
   - **Only the user narrows this.** If they said free/local in this conversation, obey it and
     pick the best model that fits the probed VRAM at the smallest variant that does the job.
     If they did not say it, do not infer it and do not default to free — say in one line that
     the pick is paid and keep building, offering the free alternative as a `suggest` chip.
   - **A paid key lives in Settings, not the chat.** Emit `${NAME}` where the key goes; `comfy_run`
     substitutes it at submit time so the secret never lands in a workflow file.
1. `comfy_research("<model name>")` — find the repo. A `.json` in the publisher's repo is
   usually their **reference workflow**: fetch it by URL with the same tool. That file is the
   answer, written by the people who trained the model. **For a PARTNER node the reference
   workflow is Comfy's own**: `web_fetch` `https://docs.comfy.org/tutorials/partner-nodes/<provider>/…`
   or the matching `api_*` workflow on comfy.org/workflows, and `comfy_node_spec` the node —
   Civitai and Hugging Face hold nothing for a hosted model.
2. No reference workflow? Fetch the model card / README and pull the facts out: loader, text
   encoder(s), VAE, latent node, sampler/scheduler/steps/cfg/shift. For anything not on HF or
   Civitai, `web_search` the release announcement and fetch what it links.
3. **Community pass**: `web_search` the chosen stack against Reddit, GitHub issues and blogs —
   required companion files, known pitfalls, settings that actually work at this VRAM. Then
   cross-validate: two independent sources must agree on the architecture and file list before
   any graph is drawn.
4. **Emit first, validate, THEN install — in that order, always.** `comfy_emit` the graph, then
   `comfy_validate` the `.api.json` — every node class, link and filename checked against the
   instance; its missing-file list is the ONLY `comfy_install` shopping list. Never install
   before validate: guessing at files and building around whatever downloaded is how a run burns
   on the wrong weights. A download in flight is not a failure — wait and re-check
   `comfy_inventory`; never re-queue a file already downloading, never punt because it is slow.
   The server settles anything left: `node_errors` on submit names the exact input and the
   values this instance accepts.

**The user swapping models is a return to step 1**, not an edit — unless it is the same family
(one SDXL fine-tune for another).

## One family's shape, as a worked example

The classic SD/SDXL checkpoint backbone — an example of what a family's wiring looks like,
**not a template for other families**. Give `comfy_emit` a node list in this shape; ids are
yours to choose, keep them stable across iterations so a diff is readable.

**Text to image:**

```
CheckpointLoaderSimple  -> MODEL, CLIP, VAE
CLIPTextEncode (positive)  clip <- CLIP
CLIPTextEncode (negative)  clip <- CLIP
EmptyLatentImage        -> LATENT     (width, height, batch_size)
KSampler                model <- MODEL, positive <- pos, negative <- neg, latent_image <- LATENT
VAEDecode               samples <- KSampler, vae <- VAE
SaveImage               images <- VAEDecode
```

**Image to image** — replace `EmptyLatentImage` with `LoadImage -> VAEEncode`, and set the
sampler's `denoise` below 1.0 (0.4–0.7 is the usual range; lower keeps more of the original).

**With a LoRA** — insert `LoraLoader` between the checkpoint and everything downstream, and
route **both** MODEL and CLIP through it. A LoRA wired to the model but not the text encoder is
the classic half-applied result.

**Upscale** — `UpscaleModelLoader` + `ImageUpscaleWithModel` after `VAEDecode`, or a second
`KSampler` pass at higher resolution with low `denoise`.

**ControlNet** — `ControlNetLoader` + `ControlNetApply` between the text encode and the sampler;
the hint image comes from `LoadImage` through whatever preprocessor that pack provides.

`SaveImage` writes to the instance's output folder and is what makes a run produce anything.
`PreviewImage` writes to `temp` — use `SaveImage` unless the user asked otherwise.

## Image inputs — chat to instance

A `LoadImage` node's `image` input is an **enum of what is in the instance's input folder** —
a local path is not a legal value, and a name not yet uploaded fails `value_not_in_list`. So
the order is fixed: `comfy_upload` first, then emit with the names it returned. Chat
attachments arrive in `uploads/` in the workspace; upload straight from there.

Multi-image workflows (image-to-video first/last frame, reference + mask, several ControlNet
hints) each take their own `LoadImage` node — one per role, each wired to the socket its role
feeds. Confirm the file-to-role mapping with the user before wiring; filenames do not carry
intent. On iteration an already-uploaded image is still there — only re-upload what changed.

## Reading the instance

- `comfy_inventory` — every model file any loader can see, reported as `NodeClass.input_name`
  groups. **Use these names and nothing else.** The grouping tells you the loader for free:
  a file under `UNETLoader.unet_name` is a bare diffusion model that needs its encoders and
  VAE loaded separately; one under `CheckpointLoaderSimple.ckpt_name` is self-contained.
- `comfy_node_spec <class>` — one node's real inputs. The `input` map has `required` and
  `optional`; each entry is `[type, config]`. A type that is a **nested array** is an enum and
  that array is the list of legal values — that is how you learn this instance's samplers and
  schedulers, and how you check a value before spending a run on it.
- VRAM from `comfy_probe` bounds resolution and batch size; weigh it against what research
  said about the model's appetite.

## When it is rejected

`POST /prompt` returns 400 with `node_errors` keyed by node id. The `type` tells you what to do:

| type | what it means | the fix |
|---|---|---|
| `value_not_in_list` | that name is not on this instance | use one from `comfy_inventory`; the error's `details` lists the valid ones |
| `missing_node_type` | that node's PACK is not on this instance | first check it is not just a wrong class NAME; if the pack is genuinely missing, `comfy_node_install` it (it restarts ComfyUI), then re-probe and re-check the class |
| `required_input_missing` | an input was left out | `comfy_node_spec` shows what is required |
| `return_type_mismatch` | a link joins incompatible sockets | check which output slot you linked |
| `bad_linked_input` | a link is not `[id, slot]` | fix the shape |
| `value_smaller_than_min` / `value_bigger_than_max` | out of range | the spec carries `min`/`max` |

A 200 with a `prompt_id` is queued. Non-empty `node_errors` **on a 200** is a warning about a
pruned branch, not a failure — mention it, do not panic.

## Timing and results

`comfy_run` waits for the run and returns the output manifest: node, filename, subfolder, type.
Hand those entries to `comfy_download` verbatim — it pulls the rendered files into the
workspace and they render in the chat as artifacts, so the user sees the result without opening
their instance. (`type` matters: `SaveImage` outputs are `output`, `PreviewImage` writes
`temp`.) A first run on a cold model can take minutes; a timeout means still-running, not
failed.

## Field guide — where the sweep starts (2026-09-13)

**What this is.** The current floor per task: the models that define "good" today, paid and
open, and the one property that makes each fit. The research sweep (AGENTS.md step 3) STARTS
here and CONFIRMS or BEATS it — it never lands below it unless the user asked for cheaper or
free, or the probed VRAM cannot carry the open pick. The wiring is still researched per family;
this file names models, not graphs.

**Dated on purpose.** Models turn over in months. If today is more than three months past the
date above, do one `web_search` for successors of the picks below before trusting them, and say
so in the plan.

**Names, not guesses.** Every paid pick here is a ComfyUI partner node on the rented image.
Get the real class with `comfy_node_search "<provider or model>"` — it also flags `deprecated`
(use the successor) and `api_node` (paid). `comfy_price` prices any of them. Never emit a class
you have not seen in a search or a spec.

## Identity-locked stills — one reference, many shots (storyboards, angles, outfits)

OPEN FIRST. The box is a rented RTX-class card with ~30 GB free; a current open model that meets
the brief there IS the pick, and it costs the user nothing. A paid service has to buy something
specific — more references than the open pick takes, a capability it lacks, quality the user
asked for by name — and the ask says what it buys, beside a free row for the open route.

Ranked by the open-weights IMAGE-EDITING arena (Artificial Analysis, read 2026-09-13), because
"the same person in a new scene" is an edit conditioned on a reference, not a text-to-image draw:

| pick | why | how it is reached |
|---|---|---|
| open: **FLUX.2 [klein] 9B** (Black Forest Labs, Jan 2026) | #2 open editing (Elo 1004); Apache-2.0; references in, strong identity and layout preservation; light and fast on the card. The 4B is a 4-step distil for previews | native FLUX.2 klein nodes; weights on Hugging Face (black-forest-labs) |
| open: **Qwen-Image-Edit-2511** (Alibaba, Dec 2025) | #4 open editing (Elo 1000); Apache-2.0; up to 3 references, multi-angle views from ONE reference, face identity kept across pose and style; 20B, FP8 ≈ 16 GB | native Qwen-Image-Edit nodes (docs.comfy.org: qwen-image-edit-2511) |
| open: **HiDream-O1-Image** (HiDream, May 2026) | #9 open editing (Elo 951); MIT; unified generation + editing with a reference, pixel-native; FP8 ≈ 10 GB | native (docs.comfy.org: hidream-o1) |
| open: **FLUX.2 [dev]** (Nov 2025) | #3 open editing and #3 open text-to-image (Elo 1000); multi-reference editing at full size; heavier than klein — take it when klein drifts | native FLUX.2 nodes; weights on Hugging Face |
| open: **Z-Image Turbo** (Tongyi, 6B, Nov 2025) | #17 open text-to-image (Elo 932) but the community's photoreal-skin favourite: 8 steps, ≈ 2 s an image at 1024 — the DRAFT and text-to-image engine. It takes NO reference: Z-Image-Omni-Base and Z-Image-Edit are "to be released" (Tongyi-MAI/Z-Image README). For identity on Z-Image only IPAdapter-FaceID-style adapters exist, an SD-era method — prefer the rows above when the face must hold, and switch to Omni the day its weights land | native Z-Image nodes |
| open, not a default: **HunyuanImage 3.0 Instruct** (Tencent) | #1 open editing (Elo 1027) but an 80B MoE: NF4 wants a 48 GB card, and the ~20 GB INT8 distil is a community quant behind a custom node pack. Only when the box is big enough and the user wants the top of the chart | custom nodes (Comfy_HunyuanImage3) |
| open, not for adverts: **Ideogram 4.0** (Jun 2026) | #1 open text-to-image (Elo 1017), design and typography; NON-COMMERCIAL licence — an advert is commercial work | native |
| **Nano Banana Pro** (Gemini 3 Pro Image) | paid: up to 14 references, the strongest identity lock across many scenes (editing arena 1094) — buy it when the open picks drift, or the job needs more than 3 references | `GeminiImage2Node`, model `gemini-3-pro-image-preview`; references via a Batch Images node |
| **Seedream 5.0 Pro** | paid: editing arena 1100; precise edits, layout, text; 10–14 references; layer separation | `ByteDanceSeedreamNodeV3` (`ByteDanceSeedreamNode` is deprecated) |
| **Flux.2 [max] / [pro]** · **Flux VTO** | paid: up to 8–9 references; **`FluxVTONode` puts a garment on a person** (the try-on pick) | `Flux2ImageNode` — pro or max by input (`Flux2ProImageNode` and `Flux2MaxImageNode` are deprecated) |

**Name the row you are picking and why the next one down is not better for THIS job.** A model
the user asks for by name ("use Z-Image", "no Flux") is design input, not taste to argue with:
take the best route that honours it and say what it costs in fidelity, if anything.

**One photo is not a dataset.** Never train a LoRA from a single reference, and never generate
a "dataset" to train one from — that is a week of drift compressed into an afternoon. A LoRA is
for when the user HAS twenty to thirty photos and says so; otherwise the reference goes in a
slot and a reference-conditioned model above reads it at generation time.

Below the floor: SDXL + IP-Adapter / InstantID / FaceID, SD1.5 anything. They were the answer in
2024; today they are the reason a storyboard drifts between frames.

## Image-to-video and reference-to-video — best quality

| pick | why | how it is reached |
|---|---|---|
| **Seedance 2.5 / 2.0** | the consistency model: 2.0 takes 9 images + 3 videos + 3 audio; 2.5 goes to 30s and 4K; a spoken line in the prompt lip-syncs | `ByteDance2FirstLastFrameNode` (first/last frame + refs), `ByteDance2TextToVideoNode` |
| **Wan 3.0 / 3.0 Prime** | arena #1 (2026-08); reference images, videos AND audio in one node; up to 30s. Hosted only — no open weights | `Wan3ReferenceToVideoApi`, `Wan3ImageToVideoApi` |
| **Veo 3.1** | the safest all-rounder; synced dialogue and 48 kHz speech; 4–8s | `Veo3VideoGenerationNode`, `Veo3FirstLastFrameNode` (models `veo-3.1-generate` / `-fast-generate` / `-lite`) |
| **Kling 3.0 Omni / Turbo** | multi-shot storyboards in one node, native audio, lip-sync in five languages; Turbo is the value pick | `KlingVideoNode` (`kling-v3`, `kling-3.0-turbo`); `KlingImageToVideoWithAudio` is Kling **2.6** — not v3 |
| **MiniMax H3 Max** | expressive motion; reference-to-video with 9 images + 3 videos + 3 audio | `MinimaxHailuo03ReferenceNode`, `MinimaxHailuo03FirstLastFrameNode` |
| open: **MiniMax H3** (FL2VA / Ref2VA) | open weights since 2026-08; 768p with native stereo audio; reference-to-video on the box | native `MiniMaxH3ImageToVideo`, `MiniMaxH3ReferenceToVideo` (19.5–62 GB of weights) |
| open: **Wan 2.2 14B** | best faces, skin and hair among open models; official templates | native Wan 2.2 nodes; Wan-Animate-2 for character animation |
| open: **LTX-2.5** | native audio-video in one pass, multi-shot continuity, IC-LoRA control | ComfyUI-LTXVideo |

Below the floor: AnimateDiff, SVD, Wan 2.1, HunyuanVideo 1.0. **Sora 2 is retired** (API off
2026-09-24) — never pick it.

## Talking head — one photo, a line, lip-sync

| pick | why | how it is reached |
|---|---|---|
| **Kling 3.0 Omni** | dialogue in quotes in the prompt → lip-synced speech; `KlingLipSync*` re-syncs an existing clip to audio | `KlingVideoNode`; `KlingLipSyncAudioToVideoNode` / `KlingLipSyncTextToVideoNode` |
| **Seedance 2.x** | the spoken line in the prompt, native audio, identity from references | as above |
| **Veo 3.1** | the most natural speech; dialogue is what it owns | as above |
| **HeyGen** · **sync.so** | avatar from a still · lip-sync a finished video | their partner nodes |
| voice | **ElevenLabs TTS** node for a scripted line the model then syncs to | `ElevenLabs` partner node |
| open: **MiniMax H3 Ref2VA** · **Wan 2.2 S2V** | audio reference drives the mouth on the box | native H3 nodes; `WanSoundImageToVideo` |

## Upscale and finish

**Topaz** (image and video), **Magnific** (image), **Flux Video Upscale**; open: SeedVR2 (video),
4x-UltraSharp / RealESRGAN (image). A finish pass is offered, not assumed, unless the user asked
for a resolution the generator cannot do natively.

## The rules this file carries

1. **Start here, then research.** The sweep confirms the pick and finds the wiring; it does not
   rediscover 2024.
2. **Free is a choice the user makes**, not a default. "Best quality" means the top of the paid
   column; say the credits and let the ask (`ask_user`) decide.
3. **A deprecated node is a wrong node.** The search shows the successor on the same instance.
4. **The version in the graph is the version the user approved.** Kling 2.6 is not Kling v3.
5. **Partner nodes are researched at the source** — `docs.comfy.org/tutorials/partner-nodes/<provider>`
   and Comfy's own `api_*` workflow templates — never Civitai, which holds nothing for them.

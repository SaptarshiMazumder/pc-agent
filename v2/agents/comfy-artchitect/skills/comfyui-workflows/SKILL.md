---
name: comfyui-workflows
description: Use when designing, emitting, running or repairing a ComfyUI workflow — how to research what a model family needs, the two JSON formats, and how to read what an instance rejected.
always: false
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

0. **Which model at all?** Before committing, sweep the landscape: `web_search` ("best open
   <task> model <year>", "<task> comfyui workflow") plus `comfy_research` search on Hugging Face
   and Civitai. Note for each candidate whether it is **FREE/local** (open weights you download
   and run on the user's GPU) or **PAID/API** (a cloud node — Seedance/ByteDance, Kling, Runway —
   that calls an external paid service and needs a key).
   - **When the best options split free vs paid, ASK the user which they want** — it is a real
     cost decision and the paid path needs a key only they have. Default to free if they don't
     care; never block on the answer.
   - **Free** → the best LOCAL model that fits the probed VRAM at the **SMALLEST variant that does
     the job** (fp8/5B over an fp16 the card cannot hold — a 31 GB GPU won't run two 28 GB fp16
     experts). Size is part of the choice; tens of GB you can't fit or finish downloading is a
     failed choice.
   - **Paid** → have the user paste the provider key in chat; `comfy_node_spec` the API node and
     wire the key into its input if it takes one, else point them at ComfyUI's API-key setting.
1. `comfy_research("<model name>")` — find the repo. A `.json` in the publisher's repo is
   usually their **reference workflow**: fetch it by URL with the same tool. That file is the
   answer, written by the people who trained the model.
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

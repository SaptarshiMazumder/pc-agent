# HiDream-I1 / HiDream-E1 — the agent's guide

HiDream.ai's 17B sparse-DiT (MoE) image family: **I1** text-to-image in Full / Dev / Fast variants and **E1 / E1.1** instruction-based editing. Big: every variant drives **four text encoders** (CLIP-L, CLIP-G, T5-XXL, Llama-3.1-8B — ~16 GB of files) and the fp8 diffusion files "require more than 16GB of VRAM" (bf16 "more than 27GB"). Everything below was read from the official templates, docs and model cards on 2026-10-01; `profile.json` holds the exact files, numbers and sources.

## When to pick it, and which recipe

- **Top prompt following / text / Chinese prompts, ≥24 GB:** `t2i-i1-full` — 50 steps, cfg 5 with a real
  negative, uni_pc/simple, shift 3. "Best-in-Class Prompt Following" (GenEval / DPG).
- **Balanced:** `t2i-i1-dev` — distilled, 28 steps, cfg 1, lcm/normal, **shift 6**; no negative.
- **Preview:** `t2i-i1-fast` — distilled, 16 steps, cfg 1, lcm/normal, shift 3. Same 17 GB file size as
  Full: fast in steps, not in VRAM.
- **Instruction editing:** `edit-e1-1` — 1 MP dynamic size, direct instructions, "better in image quality
  and editing accuracy compared to HiDream-E1-Full"; loads the bf16 file as `fp8_e4m3fn_fast` because fp16
  OOMs on a 24 GB 4090D and a 40 GB A100.
- **Legacy 768² editing:** `edit-e1-full` — only works at 768×768, 34 GB bf16 at default dtype.
- **Not this family** for ControlNet, IP-Adapter, inpaint or img2img (none exist), or on cards under
  16 GB without GGUF (community: Q8 fits 24 GB; needs ComfyUI-GGUF). HiDream-O1 is a different, later model.

## What the recipes are (one line each)

| recipe | needs | does |
|---|---|---|
| `t2i-i1-full` | prompt + negative | 1024², 50 steps, cfg 5, uni_pc/simple, shift 3 |
| `t2i-i1-dev` | prompt | 28 steps, cfg 1, lcm/normal, shift 6 |
| `t2i-i1-fast` | prompt | 16 steps, cfg 1, lcm/normal, shift 3 |
| `edit-e1-1` | image + instruction | 1 MP, CFGNorm, InstructPixToPixConditioning, DualCFGGuider 3 / 1.5, 20 steps euler/simple |
| `edit-e1-full` | image + instruction | forced 768², DualCFGGuider 5 / 2, 28 steps euler/normal |

Disk: one fp8 I1 file (17.1 GB) + four encoders (15.9 GB) + VAE ≈ 33 GB; each E1 file adds 34.2 GB.

## Prompting

**Negative prompt:** I1-Full template `bad ugly jpeg artifacts` (only Full uses it; Dev/Fast run at cfg 1).
E1 / E1.1: `low quality, blurry, distorted` (the official E1.1 script's negative_instruction).

**I1 positive:** natural-language scene descriptions; text via a quoted string (*'A cat holding a sign that
says "HiDream.ai".'*); Chinese prompts are a strength.

**E1.1:** one direct instruction plus what must stay — *"Change the image to let the girl's hair fall loose
around her shoulders, natural and flowing. Don't change other parts"*. "Prompt refinement is no longer needed."

**E1-Full:** the exact format `Editing Instruction: {instruction}. Target Image Description: {description}`
— e.g. *"Editing Instruction: Convert the image into a Ghibli style. Target Image Description: A person in a
light pink t-shirt with short dark hair, depicted in a Ghibli style against a plain background."*

Docs: "try to make your prompts as complete as possible"; "You may need to modify the prompt multiple times
or generate multiple times". What hurts: style-change edits (E1 loses consistency), non-768 inputs on
E1-Full, vague instructions, negatives / cfg > 1 on Dev and Fast.

## Settings that matter (the validator enforces the hard ones)

- **Encoders:** `QuadrupleCLIPLoader` with **all four**: `clip_l_hidream`, `clip_g_hidream`,
  `t5xxl_fp8_e4m3fn_scaled`, `llama_3.1_8b_instruct_fp8_scaled` (order l,g or g,l both load).
- **VAE:** FLUX `ae.safetensors`.
- **Per variant** (`ModelSamplingSD3` shift / steps / sampler / cfg): **Full 3 / 50 / uni_pc simple / 5**;
  **Dev 6 / 28 / lcm normal / 1**; **Fast 3 / 16 / lcm normal / 1**. Dev at shift 3 or Full at 6 is a mis-setting.
- **E1-Full:** input `ImageScale` bilinear **768×768** center; `DualCFGGuider` regular, text 5 / image 2
  (official 5.0 / 4.0); 28 steps.
- **E1.1:** input `ImageScaleToTotalPixels` 1 MP; `CFGNorm` strength 1 (= clip_cfg_norm); `DualCFGGuider`
  text 3 / image 1.5; `UNETLoader` weight_dtype **fp8_e4m3fn_fast** under ~40 GB; 20 steps (official 28).
- **Sizes:** 1024² in every I1 template (other sizes NOT FOUND officially); multiples of 16.
- **Template quirk:** the E1.1 template wires the *Positive* encode into `DualCFGGuider.negative` (E1-Full
  wires the Negative encode, as does the official script). The recipe keeps the template; rewire to the
  Negative encode if the negative must act.

## When it goes wrong

| symptom | fix |
|---|---|
| out of memory | fp8 I1 files; E1.1 fp8_e4m3fn_fast; Q8 GGUF (needs pack); offload encoders |
| encoder load fails | all four Comfy-Org encoders through QuadrupleCLIPLoader |
| Dev / Fast washed or wrong | cfg 1, no negative |
| Dev looks off | shift 6 (Dev) vs 3 (Full / Fast) |
| E1-Full edit poor or different | input 768×768 |
| E1.1 aspect changed | crop/resize before the stage |
| E1.1 differs from the demo | official pipeline adds a refine_strength 0.3 I1-Full pass; template 20 vs 28 steps |
| E1.1 negative seems ignored | template wiring; rewire DualCFGGuider.negative to the Negative encode |

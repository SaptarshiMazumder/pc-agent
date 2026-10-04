# Kandinsky 5.0 Video — the agent's guide

Kandinsky Lab's (Sber) open-weights video family, free for anything. Video **Lite** is a 2B DiT (4.6 GB) that does text-to-video and image-to-video at 768×512, 5 s or 10 s @24 fps, from **English or Russian** prompts; Video **Pro** is a 19B DiT (43 GB bf16) with no ComfyUI template yet. Silent (no audio). Everything below was read from the official templates, the kandinskylab repo and configs on 2026-10-01; `profile.json` holds the exact files, numbers and sources.

## When to pick it, and which recipe

- **Pick it** for a cheap, small video model (one 4.6 GB checkpoint + the 9.4 GB Qwen encoder — ~15 GB
  on disk, every speed variant is another 4.6 GB), for **Russian-language prompts** ("best understanding
  of Russian concepts in the open-source ecosystem"), for 10 s clips from one pass (Lite 10 s), or for a
  fast draft (16-step distilled). The publisher's own ComfyUI pipeline ran "at 24 GB with offloading";
  the reference code on 12 GB with NF4 Qwen; native-Comfy VRAM is NOT FOUND.
- **Not this family** for audio, I2V longer than 5 s (no 10 s I2V Lite), 720p+ output (Lite is a
  768×512-class model), named camera moves in native ComfyUI (the camera LoRAs' Comfy compatibility is
  NOT FOUND), or Pro quality on a 24–32 GB card (43 GB bf16, no quantised file, no template).

| recipe | needs | does |
|---|---|---|
| `t2v-lite-5s` | prompt | the quality Lite: SFT, 768×512×121, 50 steps, cfg 5, shift 5 |
| `t2v-lite-5s-nocfg` | prompt | CFG-distilled: cfg 1, 50 steps, "2× faster" |
| `t2v-lite-5s-distilled` | prompt | distilled16steps: cfg 1, 16 steps, "6× faster", "minimal quality loss" |
| `t2v-lite-10s` / `-nocfg` / `-distilled` | prompt | the 10 s models: 241 frames, shift **10**, width/height divisible by **128** (768×512 qualifies) |
| `i2v-lite-5s` | start image | image scaled to 768×512; clean first-frame latents put back and normalised before decode; template shift 5 (publisher says 10 — port exposed) |

Pending / not offered: Pro T2V/I2V (no template, 43 GB, community says >32 GB without offload);
camera-control LoRAs (Arc / Dolly / Microwave / Truck — Comfy compatibility NOT FOUND); the pretrain
checkpoints ("designed for fine-tuning"); Kandinsky WM 1.0 (Physical-AI I2V, Comfy loadability NOT
FOUND). The Image Lite T2I template is an image family, not converted here.

Stage composition the user usually wants: a still from an image model → `i2v-lite-5s` (give it a 3:2
image; the template center-crops to 768×512); a quick draft → `t2v-lite-5s-distilled`; the final →
`t2v-lite-5s` or the 10 s SFT.

## Prompting

**Negative prompt: empty** in both official templates. The publisher's reference pipeline uses
`Static, 2D cartoon, cartoon, 2d animation, paintings, images, worst quality, low quality, ugly, deformed, walking backwards`
— use it if you want one; it only matters at cfg > 1 (SFT), never on the nocfg / distilled files.

**Positive prompt:** there is no official handbook (NOT FOUND). The official template prompts follow one
pattern: **comma-separated tags first** — light, shot size, time of day, composition, colour, lens
("Rim light, side light, soft light, medium close-up, dusk, sunset, central composition, warm tones with
low saturation, telephoto lens.") — **then one prose paragraph** of subject, action, camera motion and
atmosphere. For I2V the image is the subject; spend the prompt on camera verbs and motion rhythm ("The
camera glides slowly forward … As the lens pans right … The camera then pulls back softly"). English or
Russian. Short prompts work in the reference code ("The bear plays balalaika.") but the templates use
long cinematic paragraphs.

What hurts: a negative prompt on cfg-1 checkpoints (skipped); a start image far from 3:2 (cropped);
implying stillness or backwards motion (both in the publisher's own negative).

## Settings that matter (the validator enforces the hard ones)

- **Encoders:** `DualCLIPLoader` type **`kandinsky5`** with `qwen_2.5_vl_7b_fp8_scaled` + `clip_l`
  (`kandinsky5_image` is for Image Lite; `hunyuan_video_15` is HunyuanVideo's — same Qwen file,
  different tokenizer).
- **VAE:** the **HunyuanVideo 1.0** VAE `hunyuan_video_vae_bf16` (16-ch, 8×, from Kijai) — never the
  HunyuanVideo 1.5 VAE (`hunyuanvideo15_vae_fp16`, 32-ch, 16×).
- **cfg:** 5 for SFT/pretrain, **1 for nocfg and distilled16steps**. **steps:** 50, or **16** for
  distilled16steps.
- **shift (ModelSamplingSD3, "Scheduler Scale"):** 5 for the 5 s T2V, **10 for the 10 s models and
  (per the publisher) I2V**. The Comfy I2V template uses 5 — disagreement recorded; the recipe ships 5,
  try 10 if motion or colour is off. The node default 3 is wrong for Kandinsky.
- **length:** 4n+1 — 121 (5 s) or 241 (10 s models); 5 s checkpoints stop at 5 s. **fps** 24.
- **width/height:** multiples of 16; 768×512 is the documented size; **divisible by 128 for the 10 s
  models** (NABLA sparse attention).
- **I2V graph:** `ImageScale` to the latent size → `Kandinsky5ImageToVideo.start_image`; after the
  sampler, `ReplaceVideoLatentFrames(index 0, source = cond_latent)` then `NormalizeVideoLatentStart
  (4 / 5)` before `VAEDecode`. No clip-vision: I2V conditions through the VAE-encoded first frame only.
- Sampler `euler_ancestral` / `beta` (video templates).

## When it goes wrong

| symptom | fix |
|---|---|
| prompt barely followed | `DualCLIPLoader` type `kandinsky5` |
| channel / shape error | HunyuanVideo **1.0** VAE, not the 1.5 one |
| 10 s model fails or degrades | 768×512 (128-multiples) × 241 frames; shift 10 |
| I2V motion / colour off | shift 10 (publisher) instead of the template's 5 |
| burnt, over-guided | cfg 1 on nocfg / distilled; 16 steps on distilled16steps |
| seam after the first frames (I2V) | keep `ReplaceVideoLatentFrames` + `NormalizeVideoLatentStart` |
| I2V output cropped | feed a 3:2 image; output follows the image's aspect, area ≈ 768×512 |
| Pro will not fit | use Lite; Pro recipe pending |
| wrong folders / missing nodes | the publisher's old custom-node layout mixed with the native templates — use the native layout only |
| camera LoRA will not load | Comfy compatibility NOT FOUND; describe the move in prose |

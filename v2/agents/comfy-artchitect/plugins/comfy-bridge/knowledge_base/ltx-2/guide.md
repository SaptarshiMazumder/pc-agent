# LTX-2 family (LTX-2.5 / LTX-2.3) — the agent's guide

Lightricks' open-weights **audio-video** DiT: every clip comes with synchronized sound (dialogue, lip-sync, SFX, music) from the same model. LTX-2.5 (22B, 2026-08-11) is the current generation; LTX-2.3 (22B, 2026-03) is "Previous Generation" and still carries the only official templates for lip-sync-to-supplied-audio, ID-LoRA voice cloning, IC-LoRA control and reference sheets; the 19B LTX-2 is legacy and not converted. Everything below was read from the official templates, model cards and ComfyUI source on 2026-10-01; `profile.json` holds the exact files, numbers and sources.

**Download gate:** `Lightricks/LTX-2.5` (and the 2.5 IC-LoRA, 2.3 Ingredients and Pre-Trained repos) are gated on Hugging Face — or the download fails with 401/403. `Lightricks/LTX-2.3`, `LTX-2.3-fp8`, `Comfy-Org/ltx-2.3`, `Comfy-Org/ltx-2` and `Comfy-Org/gemma-4` are open.

## When to pick it, and which recipe

- **Pick it** when the clip needs sound (speech with lip-sync, music, ambience), dialogue in quotes,
  multi-shot sequences from one prompt (2.5), a first+last-frame transition with audio, or
  identity/voice from one image and ~5 s of audio (2.3 ID-LoRA). 24 fps (2.5) / 25 fps (2.3); 5 s =
  121 frames per clip; 1280×704 default, up to 1920×1088 in the 2.5 size table.
- **Not this family** for text-to-audio alone or clip extension in core ComfyUI (both need the
  Lightricks custom nodes / have no template), for cards under 24 GB without offloading, or when the
  2.5 weights cannot be fetched (gated) — then the 2.3 route, which wants 32 GB+.
- **2.5 vs 2.3:** 2.5 is the quality default and the lighter box (int8 transformer 21.5 GB + Gemma 4
  15.4 GB; a 3090 24 GB runs 1280×720/5 s). 2.3 is one 27–29 GB fp8 checkpoint + Gemma 3 9.4 GB and the
  only route for IA2V, ID-LoRA, IC-LoRA control, Ingredients and the style-transition LoRA. Files and
  LoRAs are **not interchangeable** between the two.

| recipe | needs | does |
|---|---|---|
| `t2v-2.5` | prompt | 5 s 1280×704 @24 fps with audio; two-stage (half-res 8 sigmas → x2 latent upsample → 3 sigmas); multishot by prompt |
| `i2v-2.5` | start image | animates the image (in-place strength 0.7 / 1.0), CRF-18 preprocess |
| `flf2v-2.5` | start + end image | one motion between two keyframes, single stage at full size, no upsampler file |
| `t2v-2.3` / `i2v-2.3` | prompt / start image | the previous generation: dev-fp8 + distilled LoRA 0.5, Gemma 3, 25 fps |
| `flf2v-2.3` | start + end image | needs the **distilled** 2.3 checkpoint (a different 27.5 GB file from the dev-fp8) |
| `ia2v-2.3` | start image + speech audio | lip-sync to supplied audio (audio latent encoded and frozen); the only official IA2V template |
| `id-lora-2.3` | face image + ~5 s reference voice | face + cloned voice; speech is generated from `[SPEECH]:` |
| `control-depth-2.3` / `control-canny-2.3` | first frame + control video | IC-LoRA Union Control; depth via core MoGe nodes, or Canny |
| `ingredients-2.3` | reference sheet image | characters / props / location locked to a sheet (≥121 looped frames) |
| `style-transition-2.3` | start + end image | FLF2V + community transition LoRA, trigger word `zhuanchang` |

Pending / not offered: pose control (needs comfyui_controlnet_aux), the six 2.3 editing templates
(need ComfyUI-LTXVideo, kjnodes, VHS, ComfyMath and mostly the 46 GB bf16 dev file), audio-to-video on
the 19B (kjnodes + custom LTX nodes), text-to-audio (ComfyUI-LTXVideo), clip extension (no template),
the dev model without the distilled LoRA (no template).

Disk: the 2.5 set ≈ 40 GB; one 2.3 route ≈ 40–43 GB; both 2.3 checkpoints + encoder ≈ 68 GB do not
fit a 60 GB disk — pick one 2.3 route per box.

## Prompting

**Negative prompt — short form (T2V/I2V/IA2V/ID templates):** `pc game, console game, video game, cartoon, childish, ugly`
The guided templates (FLF2V, IC-LoRA, Ingredients, style transition) carry the long diffusers-lineage
negative (in `profile.json`). **Every template runs at CFG 1, where the negative has no effect**; it
only matters if you raise `video_cfg`/`audio_cfg` with the dev model.

**Positive prompt (official):** "detailed, chronological descriptions of actions and scenes. Include
specific movements, appearances, camera angles, and environmental details — all in a single flowing
paragraph. Start directly with the action, and keep descriptions literal and precise. Think like a
cinematographer describing a shot list. Keep within 200 words." Order: main action in one sentence →
movements and gestures → appearance → environment → camera angle/movement → lighting/colour → changes
or sudden events. The 2.5 training-caption style adds: per shot exactly one shot type (wide / medium /
close-up …), the camera motion ("if none, explicitly say the camera remains static") and the viewpoint
(front-facing / side / over-the-shoulder / low-angle …); a complete soundscape with dialogue quoted
exactly in its language, tone of voice, music and ambience; chronological markers ("Initially…", "A
moment later…"); 150–220 words; **no labels like "Audio:"**.

- **Dialogue:** inside the prose — `She stops and says: "The old gods are silent. I am not."`
- **I2V:** describe what happens next, not what is visible; open with "Use the provided start image as
  the first frame". **FLF2V:** describe the transition; same aspect ratio for both frames.
- **Multishot (2.5):** two to four shots in one paragraph; name every cut in prose ("A hard cut
  transitions to…", "the image dissolves into…") or it is read as camera motion inside one shot;
  re-establish scale, angle, who is in frame and lighting after each cut; state audio continuity.
  (Partner docs; the official guide was not fetchable.)
- **ID-LoRA:** `[VISUAL]: … [SPEECH]: exact words [SOUNDS]: tone, volume, mic distance, ambience`.
  **IA2V:** a prose sentence then `scene:` / `character:` / `action:` / `camera:` lines.
  **Ingredients:** `### Reference Sheet Description` (inventory the panels) then `### Target Description`.
  **Style transition:** the transformation over time, then `zhuanchang` near the end.

What hurts: timestamped shot lists and "Audio:" labels; cuts without a transition marker; very short
prompts without the enhancer; relying on the negative.

## Settings that matter (the validator enforces the hard ones)

- **Frames 8n+1** (121 = 5 s @24 fps; 126 is floored to 121). **Sizes multiples of 32** (1280×704,
  not 720). **frame_rate** is one value across `LTXVConditioning`, `LTXVEmptyLatentAudio` and
  `CreateVideo` (24 for 2.5, 25 for 2.3), and the audio latent's `frames_number` equals the video length.
- **AV latent:** `LTXVConcatAVLatent(video, audio)` before the sampler, `LTXVSeparateAVLatent` after.
- **Distilled = fixed schedule, CFG 1:** stage 1 sigmas `1.0, 0.99375, 0.9875, 0.98125, 0.975,
  0.909375, 0.725, 0.421875, 0.0`; stage 2 after the x2 upsample `0.85, 0.7250, 0.4219, 0.0`; IC-LoRA
  graphs use KSampler 8 steps `linear_quadratic`. Never add CFG > 1 to a distilled model.
- **2.5 files:** `UNETLoader` transformer, `VAELoader` video VAE (`ltx-2.5-video-vae-bf16`, or the
  lighter `-conv-`), a second `VAELoader` for the audio VAE, `CLIPLoader` type `ltxv` with the Gemma 4
  "with-proj" encoder (stock Gemma 4 is rejected at load). **2.3 files:** one checkpoint to
  `CheckpointLoaderSimple`, `LTXVAudioVAELoader` and `LTXAVTextEncoderLoader` (Gemma 3).
- **Image conditioning:** `LTXVPreprocess` CRF 18 (2.5 and 2.3 T2V/I2V) or 25 (2.3 FLF2V); in-place
  strength 0.7 stage 1 / 1.0 stage 2; guides 0.7 at frame_idx 0 and -1.
- **Guided graphs:** `GetICLoRAParameters` → `LTXVAddGuide.iclora_parameters` for IC-LoRAs;
  `LTXVCropGuides` before the decode; single-stage guided templates decode `denoised_output` (slot 1).
- **Tiled decode (2.5):** tile **512**, overlap 64, temporal 64/16 — the 10 s freeze on a 24 GB card
  was fixed by 768 → 512; or swap in the conv VAE.
- **LoRAs** belong to their generation (19B / 2.3 / 2.5); most 2.3 LoRAs run on 2.5, but the 2.3
  ID-LoRAs changed the subject's identity there.

## When it goes wrong

| symptom | fix |
|---|---|
| 10 s clip hangs in VAE decode on 24 GB | `VAEDecodeTiled` tile 512 (recipes); temporal 64/16; or the conv VAE |
| prompt enhancer gives unrelated video / empty text | keep it off and write the long prompt yourself (recipes do) |
| "Input and weight inner dimensions must match" | keep sizes 32-aligned (single report, unresolved) |
| wrong face / ethnicity with ID-LoRA on 2.5 | run `id-lora-2.3` |
| shape errors after mixing files | 2.3 single-file vs 2.5 split pack; LoRA from the other generation |
| int8 convrot slow / failing | needs ComfyUI ≥ 0.27 with cu130 PyTorch; fp8 2.3 checkpoints otherwise |
| negative prompt does nothing | expected at CFG 1 |
| length rejected / odd size | 8n+1 frames, multiples of 32; guide `frame_idx` divisible by 8 |
| extra frames at the end of a guided clip | `LTXVCropGuides` before decode |
| black / grey frames, OOM on 2.3 fp8 | offloading, 768×512, fewer frames (community) |
| control ignored | `GetICLoRAParameters` wired; control frames frame-aligned; strength 1 |
| Ingredients identity drifts | clean big panels on black, no text; ≥121 looped frames; strength up to 1.4 |
| weak cloned voice | ~5 s reference audio; `identity_guidance_scale` 3 |
| non-speech audio sounds poor | 2.3 card limitation — describe music/SFX explicitly or supply the audio (`ia2v-2.3`) |
| output is 704 tall, not 720 | expected: the 32-px grid |

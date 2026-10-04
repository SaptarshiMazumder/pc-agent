# Krea 2 — the agent's guide

Krea AI's open image model, trained from scratch for aesthetic quality and stylistic range (painterly,
illustrated, graphic and photographic looks). Two builds: **Turbo**, 8-step distilled, for making
images; **RAW**, the undistilled base, for training. Krea: "TRAIN on Raw and RUN on Turbo". Pick it
for stylised or art-directed stills, and to RECREATE Civitai images whose base is `Krea 2`.

## Which recipe

- `t2i-turbo`: the official template (Turbo fp8_scaled): 8 steps, cfg 1, euler/simple, 1024x1024,
  no negative (ConditioningZeroOut). The default.
- `t2i-turbo-int8`: the same graph on the int8 ConvRot build ("better quality than FP8 while
  generally running faster across most GPUs").
- `t2i-raw`: RAW bf16 at Krea's published settings: 52 steps, cfg 4.5 (Krea's `--cfg 3.5`; ComfyUI's
  cfg is Krea's + 1), a real negative, and Krea's resolution-shifted schedule (ModelSamplingFlux). Use it
  only when a record says RAW (about 50 steps, cfg above 1).

Comfy Cloud has every Krea 2 build (the `unet_name` port swaps it): Turbo fp8_scaled, int8_convrot,
bf16, mxfp8 and nvfp4; RAW bf16, fp8_scaled and int8_convrot. On Turbo, keep steps 8 and cfg 1.

## Prompting

Natural language. Long, detailed prompts work best, but short ones also give good images. Put the
words to render in quotes. The template rewrites prompts with Qwen3-VL by default. These recipes send
the prompt as written, so write the full description yourself: subject, medium or style, composition,
lighting and palette. Turbo has no negative, so write exclusions as positive wording. A Civitai
record's prompt is used as it is.

LoRAs: Civitai base `Krea 2`, loaded with LoraLoaderModelOnly after the UNETLoader. RAW-trained LoRAs
work on Turbo. Krea's nine style LoRAs are all on Comfy Cloud. Put the trigger in the prompt and use
strength 1.0 (e.g. `krea2_retroanime`: "purple retro anime style").

## Limits

Turbo outputs 1K to 2K (set megapixels to 2 for 2K). RAW is trained up to 1K. Use multiples of 16 for
width and height. Turbo ignores negatives.

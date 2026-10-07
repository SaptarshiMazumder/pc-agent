# Anima — the agent's guide

A 2B-parameter anime / illustration model by CircleStone Labs with Comfy Org, built on NVIDIA
Cosmos-Predict2-2B, with a Qwen-3 0.6B text encoder and the Qwen-Image VAE. It is for anime,
characters and other non-photorealistic art; it is deliberately bad at realism. Pick it for new anime
work, and use it to RECREATE Anima images (Civitai base `Anima`) faithfully.

## Which recipe

- `t2i-base` — the official Anima Base v1 template: anima-base-v1.0, 30 steps, cfg 4, euler/simple,
  1024x1024. The default.
- `t2i-turbo` — the same template with its turbo switch on: the official Turbo LoRA v0.2 at strength 1,
  8 steps, cfg 1. Fast; a stronger default style, less variety.
- `t2i-preview` — the official preview template: anima-preview3-base with er_sde/simple. Use it to
  recreate images made with a preview model. Set `unet_name` to the preview the image used:
  anima-preview3-base, anima-preview2 or anima-preview. Comfy Cloud has all three.

Comfy Cloud does not have Anima-Aesthetic (v1.0, v1.0b, v1.1) or the full Anima-Turbo models (v1.0,
v1.1). To recreate an image made with one of them:

1. Add the Civitai file to the stage's `models` with folder `diffusion_models` and base `Anima`.
2. Set it in `unet_name`.
3. For a Turbo model, also use cfg 1 and 8-12 steps.

Anima LoRAs (Civitai base `Anima`) go after the UNETLoader through LoraLoaderModelOnly.

## Prompting

The model takes Danbooru tags (lowercase, with spaces instead of underscores), natural language, or a
mix of both.

- Positive prefix: `masterpiece, best quality, score_7, safe, `.
- Tag order: quality/meta/year/safety tags, then 1girl/1boy, character, series, `@artist`, general tags.
- Artists need the `@`, or the tag has almost no effect.
- Write natural language as at least two sentences. Describe each named character's appearance.
- The templates' negative is `worst quality, low quality, score_1, score_2, score_3, blurry, jpeg
  artifacts, sepia`.
- Prompt weights need to be higher than on SDXL, for example `(chibi:2)`.
- On Aesthetic versions, use no score_* tags.
- When recreating a Civitai image, use its record's prompt and negative unchanged.

## Limits

- No realism.
- Only single words or short phrases of text.
- 512² to 1536² pixels. The template sizes in multiples of 16. Upscale after for larger output.
- The base model's style is plain unless you add quality or artist tags.

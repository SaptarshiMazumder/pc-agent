# Field guide — where the sweep starts (2026-09-13)

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

| pick | why | how it is reached |
|---|---|---|
| **Nano Banana Pro** (Gemini 3 Pro Image) | the identity pick: up to 14 references, the same face across poses and scenes | `GeminiImage2Node`, model `gemini-3-pro-image-preview`; references via a Batch Images node |
| **Seedream 5.0 Pro** | precise edits, layout, text; 10–14 references; layer separation | `ByteDanceSeedreamNodeV3` (`ByteDanceSeedreamNode` is deprecated) |
| **Flux.2 [max] / [pro]** | up to 8–9 references, best aesthetics per credit | `Flux2ImageNode` / `Flux2MaxImageNode` (`Flux2ProImageNode` is deprecated) |
| open: **FLUX.2 [dev]** | multi-reference editing on the box; the open identity pick | native FLUX.2 nodes; weights on Hugging Face |
| open: **Qwen-Image 2.0** | 2K native, best text rendering, unified edit | native Qwen-Image nodes |
| open: **Z-Image** (6B) | fast drafts on any card | native |

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
   column; say the credits and let the approve block decide.
3. **A deprecated node is a wrong node.** The search shows the successor on the same instance.
4. **The version in the graph is the version the user approved.** Kling 2.6 is not Kling v3.
5. **Partner nodes are researched at the source** — `docs.comfy.org/tutorials/partner-nodes/<provider>`
   and Comfy's own `api_*` workflow templates — never Civitai, which holds nothing for them.

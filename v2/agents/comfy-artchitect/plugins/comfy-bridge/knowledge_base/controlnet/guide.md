# ControlNet and structural control — the agent's guide

Not a model family: an **adapter stage** that attaches to another family's generation graph and
forces its layout (edges, depth, pose, lines, blur, inpaint region). Every recipe here is a full
minimal graph for ONE host (loader + text encode + adapter + sampler + decode + save); its
`recipe.anchor` says which node ids are the host's and which are the adapter's, so a pipeline can
splice the adapter nodes into that host's own recipe. All weights are free; the license is the
net's (and the host's) — `profile.json` has the exact files, bytes, URLs and sources (read 2026-10-01).

## When to pick it, and which recipe

Pick the host first (the image family the user wants), then the control kind:

| host | control | recipe | notes |
|---|---|---|---|
| FLUX.1 | canny | `flux1-canny-full` | the official BFL Canny model: a 23.8 GB transformer that REPLACES flux1-dev; FluxGuidance 30, cfg 1 |
| FLUX.1 | depth | `flux1-depth-lora` | 1.2 GB LoRA on flux1-dev-fp8 + native Lotus depth; FluxGuidance 10. The canny LoRA works the same way with native Canny |
| FLUX.1 | pose / tile / blur (union) | — pending | InstantX / Shakker unions: no official template; index-by-position trap (below); non-commercial |
| SD3.5 Large | canny / depth / blur | `sd35-canny` `sd35-depth` `sd35-blur` | 8.65 GB nets on the 14.9 GB fp8 checkpoint; strength 0.66-0.8; vae wired |
| Qwen-Image | canny / soft edge / depth / pose | `qwen-instantx-union-depth` | 3.5 GB InstantX union, 4-step Lightning; swap the preprocessor for the other maps |
| Qwen-Image | inpaint / outpaint | `qwen-instantx-inpaint` | the only inpainting ControlNet here; mask in the image alpha; pixels pasted back |
| Qwen-Image | canny (depth, inpaint patches) | `qwen-diffsynth-canny` | 2.3 GB MODEL PATCH on the model line — cheapest Qwen control |
| Qwen-Image | canny / depth / lineart / softedge / normal / openpose | `qwen-union-lora-canny` | 0.94 GB LoRA + ReferenceLatent on positive AND negative |
| Qwen-Image 2512 | 7 union types | `qwen2512-fun-union-canny` | native ControlNetLoader; 50 steps cfg 4 (turbo 4 / 1) |
| Z-Image-Turbo | canny / HED / depth / pose / MLSD | `zimage-fun-union-canny` | 8 steps; model patch; author says strength 0.65-0.80 |
| SDXL | any of 8 union types | `sdxl-union-xinsir` | one 2.5 GB ProMax net; COMMUNITY-CONFIRMED graph (no template) |
| SD1.5 | scribble (any control_v11 net) | `sd15-scribble-canny` | archived official example; 0.72 GB nets, smallest footprint |

Not covered on this image: Qwen-Image-2.1 Fun union (needs ComfyUI >= 0.38.0), Anima LLLite
(license NOT FOUND), T2I-Adapter and SDXL single nets (files listed, no template). Video control
lives in the video families (wan-2.2 `fun-control-14b` / `vace-14b`, MiniMax-H3 fun union, LTX-2
canny/depth/pose).

**Three wiring patterns** — know which one the host uses:
1. *ControlNetLoader → (SetUnionControlNetType) → ControlNetApplyAdvanced* on the conditioning:
   SD1.5, SDXL, SD3.5, Qwen InstantX, Qwen-2512 Fun. Wire `vae` for SD3.5 / Qwen / FLUX unions.
2. *ModelPatchLoader → QwenImageDiffsynthControlnet* on the MODEL line: Qwen DiffSynth patches,
   Z-Image Fun. Conditioning untouched; `vae` required.
3. *InstructPixToPixConditioning* (BFL FLUX Canny/Depth, full or LoRA) or *ReferenceLatent*
   (Qwen union LoRA): the control image is VAE-encoded into the conditioning; the latent comes from
   the control image, so output size = control size.

## Preprocessing — the map, not the photo

`ControlNetApply*` never converts a photo: "Each ControlNet/T2I adapter needs the image that is
passed to it to be in a specific format like depthmaps, canny maps and so on." Native options on
the box: **Canny** (every canny recipe has it in-graph; DiT models like low thresholds 0.1-0.35,
SD3.5 0.3/0.6, SD1.5 0.35/0.8), the **Lotus depth chain** (in the three depth recipes, 1.7 GB),
Depth Anything 3 / MoGe / Marigold nodes, SDPose. Everything else — openpose, DWPose, lineart,
HED/PiDiNet softedge, MLSD, normal, segment, tile — needs `comfyui_controlnet_aux` (Phase 2
install); `profile.preprocessors` has the exact class per control type (e.g. `OpenposePreprocessor`
with `scale_stick_for_xinsr_cn` enabled for the xinsir net, `DepthAnythingV2Preprocessor`,
`LineArtPreprocessor`, `AnyLineArtPreprocessor_aux` for MistoLine). SD3.5 is the exception that
post-processes the map itself (canny rescale, depth inversion).

The map must match the latent: scale it to the latent size, size the latent from it (GetImageSize)
or VAE-encode it as the latent — the recipes do one of these; a mismatched map is stretched.

## Settings that matter (the validator enforces the hard ones)

- **Union nets need `SetUnionControlNetType` with an explicit type** — `auto` skips the task
  branch. tile/repaint need the ProMax file ("Control type ... is out of range" otherwise).
- **FLUX unions (InstantX / Shakker Pro 1.0) read the INDEX, not the label**: openpose→canny,
  depth→tile, hed/pidi/scribble/ted→depth, canny/lineart/anime_lineart/mlsd→blur, normal→pose,
  segment→gray, tile→low quality. Pro 2.0 has no mode embedding — the node does nothing there.
- **`vae`** on the apply node for SD3.5, FLUX unions, Qwen InstantX/Fun, and always on
  QwenImageDiffsynthControlnet / ControlNetInpaintingAliMamaApply. Not for SD1.5/SDXL.
- **ControlNetApplyAdvanced, never the deprecated ControlNetApply** (one conditioning, hits uncond).
- **BFL FLUX Canny/Depth are not controlnet files**: UNETLoader / LoraLoaderModelOnly +
  InstructPixToPixConditioning, cfg 1, FluxGuidance 30 (canny full) / 10 (depth LoRA).
- **Strength** (publisher values, warned): xinsir 1.0; SD3.5 0.7-0.8 (templates 0.66/0.7); Qwen
  InstantX 0.8-1.0; FLUX Union Pro 1.0 0.3-0.8, Pro 2.0 per mode 0.7-0.9 with end_percent 0.8
  (0.65 pose); Fun nets stay at 1.0 (ComfyUI already sqrt-scales them); Z-Image Fun 0.65-0.80.
- **start/end_percent**: lower end frees the last steps for detail; the InstantID depth helper uses
  0-0.35 at 0.65 strength.
- **Host sampling is unchanged**: Qwen Lightning = 4 steps cfg 1; Z-Image = 8 steps cfg 1
  res_multistep; shift 3.1 (Qwen) / 3 (Z-Image); the control adds roughly its file size to VRAM
  because it runs every step (T2I-Adapters run once).
- **Chaining**: a second apply node on the first's outputs runs both nets; docs say similar
  strengths (both 1.0 in the openpose+scribble example).

## Prompting

ControlNet is prompt-independent: write the host's normal prompt for content and style; the map
decides layout. Negative belongs to the host — FLUX / Qwen-Lightning / Z-Image templates use
ConditioningZeroOut, the Qwen-2512 template ships a Chinese negative (kept in its recipe). Z-Image
Fun: a detailed prompt helps stability. Qwen InstantX: cfg 1 + res_multistep trades consistency
for speed.

## When it goes wrong

| symptom | fix |
|---|---|
| union net weak or ignores the condition | set `SetUnionControlNetType` explicitly; FLUX unions: pick by index |
| "Control type ... out of range" | use the ProMax xinsir file for tile/repaint |
| latent-hint net errors / garbage | wire `vae` into the apply node |
| "controlnet file is invalid" with flux1-canny/depth-dev | it is a UNET / LoRA: InstructPixToPixConditioning path |
| output follows a blurry photo, not a structure | you fed a raw image — preprocess it |
| Fun / Z-Image control flattens the image | strength 0.65-0.80 (Z-Image), 1.0 (Qwen Fun) |
| bad results with a big input | Qwen 1-1.68 MP or 1536 px; Z-Image enable the 1024 scale above ~2500 px |
| SD3.5 depth looks inverted | flip the map (the Lotus chain's ImageInvert output is what the template feeds) |
| stretched structure | map and latent sizes differ |
| Qwen-2.1 Fun will not load | needs ComfyUI >= 0.38.0 — use 2512 Fun or InstantX |
| commercial job | avoid FLUX ControlNets (non-commercial) and Qwen-2.1 Fun (Qwen Research); SD3.5 is Community License; xinsir / Qwen InstantX-DiffSynth-2512 / Z-Image Fun are Apache-2.0 |

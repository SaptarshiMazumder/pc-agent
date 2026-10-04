# Identity and style reference adapters — the agent's guide

Not a model family: **adapter stages** that keep a face, a character or a look across generations
by attaching to another family's graph. Every recipe is a full minimal graph for one host with a
`recipe.anchor` (host node ids vs adapter node ids, and where it splices), so a pipeline can drop
the adapter into that host's own recipe. Two kinds live here:

- **NATIVE, no pack, no insightface** (validate on the 0.35.0 image): **USO** (FLUX.1 dev; subject
  and/or style via `ReferenceLatent` + `FluxKontextMultiReferenceLatentMethod` and
  `USOStyleReference`) and **Redux** (FLUX.1 dev/schnell; "prompt with images" via `StyleModelApply`).
- **PACK-BASED** (Phase 2 install; the offline validator lists their classes as unknown): IP-Adapter
  and FaceID (SDXL / SD1.5, cubiq), PuLID-FLUX (lldacing), PuLID SDXL (cubiq), InstantID (SDXL,
  cubiq), InfiniteYou (FLUX.1, ByteDance). All need `insightface` + `onnxruntime` except plain
  IP-Adapter.

Facts, files, bytes and sources are in `profile.json` (read 2026-10-01).

## When to pick it, and which recipe

| want | host | recipe | needs | why / cost |
|---|---|---|---|---|
| same character in a reference style | FLUX.1 | `uso-subject-style-flux1` | + SigLIP 0.86 GB + 20 MB projector | "keep your character's face while changing artistic style" |
| copy a look, no face | FLUX.1 | `uso-style-flux1` | SigLIP + projector | style only; chain more style images for more strength |
| image prompting / variations | FLUX.1 | `redux-style-flux1` | 129 MB Redux + SigLIP; flux1-dev bf16 (fp8 via weight_dtype) | works with no prompt at all; not a face method |
| strongest face likeness on FLUX | FLUX.1 | `pulid-flux1` | lldacing pack, pulid_flux_v0.9.1, EVA-CLIP, antelopev2 | ~22 GB fp16 / ~12 GB fp8; weaker on some male faces |
| face + exact pose on SDXL | SDXL | `instantid-sdxl`, `instantid-sdxl-depth` | cubiq pack, ip-adapter.bin 1.69 GB + IdentityNet 2.5 GB, antelopev2 | keypoint lock; 1016x1016, CFG 4-5 |
| tuning-free face on SDXL | SDXL | `pulid-sdxl` | cubiq pack, 0.79 GB, EVA-CLIP, antelopev2 | fidelity / style modes |
| style / composition prompting | SDXL, SD1.5 | `ipadapter-sdxl`, `ipadapter-sd15` | IP-Adapter pack, PLUS adapter, ViT-H 2.5 GB | the classic; weight <= 0.8 for prompt adherence |

Pending / elsewhere: PuLID-Flux2 (FLUX.2 only, single-source project); XLabs / InstantX FLUX
IP-Adapters (beta; "not for character consistency"); and the editing families (FLUX.1 Kontext,
Qwen-Image-Edit, FLUX.2 multi-reference, HiDream O1) which keep identity by editing from the
reference without any adapter — often the better answer when the user already has the image.

**Where each adapter splices** (the anchor says it per recipe): IP-Adapter, PuLID, USOStyleReference
sit on the MODEL line between loader (after LoRAs) and sampler; Redux and the USO subject branch
wrap the positive conditioning; InfuseNet wraps positive+negative like a ControlNet; InstantID
returns MODEL + positive + negative together. PuLID-Flux goes BEFORE any TeaCache/WaveSpeed patch.

## Settings that matter (the validator enforces the hard ones)

- **Encoder pairing**: USO and Redux take `sigclip_vision_patch14_384` only. IP-Adapter: every
  SD1.5 adapter and every SDXL `*_vit-h` adapter -> `CLIP-ViT-H-14-laion2B-s32B-b79K`;
  `ip-adapter_sdxl` and `sd15_vit-G` -> `CLIP-ViT-bigG-14-laion2B-39B-b160k`. Rename the encoders
  exactly — the unified loader finds files by regex.
- **insightface models**: PuLID / InstantID / InfiniteYou want **antelopev2** (five .onnx directly
  in `models/insightface/models/antelopev2/`, no double nesting); FaceID uses buffalo_l
  (auto-download). One onnxruntime package only (CPU or GPU), never both.
- **Weights and windows**: IP-Adapter 0.8 ("0.8 minimum" for prompt adherence; FaceID 1.0 with
  weight_faceidv2 2.0, LoRA 0.6); PuLID 1.0 FLUX / 0.8 SDXL fidelity; InstantID 0.8; InfuseNet
  strength 1.0; Redux 1.0 multiply. start/end 0-1 everywhere; later start_at = more editability,
  less likeness (PuLID upstream).
- **Sampling**: FLUX adapters cfg 1 + FluxGuidance 3.5 (true CFG "performs worse" for photoreal
  PuLID); InstantID CFG 4-5 (or RescaleCFG) and 1016x1016, never 1024x1024; PuLID-SDXL cfg 6
  sgm_uniform; IP-Adapter 30 steps cfg 6.5 dpmpp_2m karras.
- **USO reference size**: 512 px keeps features; head-only crops need 1024. Style strength = how
  many style images you chain (no weight on the node).
- **Hosts**: IP-Adapter pack is SD1.5/SDXL/Kolors only; PuLID-SDXL and InstantID are SDXL only;
  PuLID-Flux, InfiniteYou, USO, Redux are FLUX.1 only.
- **Packs**: cubiq's three are maintenance-only (2025-04-14) but load; install ONE PuLID-Flux pack
  (lldacing and balazik share node ids). Pack input names flagged in each recipe's `verify[]`
  must be confirmed against `/object_info` after install.

## Prompting

The host's prompt rules apply; describe scene and style and let the adapter carry the face. If the
prompt is being ignored: IP-Adapter weight down to 0.8 and more steps, or `weight_type` "prompt is
more important"; PuLID method `style` / lower weight / later start_at. InstantID keeps copying the
reference pose unless `image_kps` gets a separate pose photo. Redux needs no prompt at all. USO
with no reference images is a plain subject-driven FLUX graph.

## When it goes wrong

| symptom | fix |
|---|---|
| "ClipVision model not found" / "IPAdapter model not found" | rename encoders exactly; keep h94's adapter names |
| tensor size mismatch | wrong adapter/encoder/base trio (vit-h vs bigG, SD1.5 on SDXL); remove the old IPAdapter_ComfyUI extension |
| "Insightface is required" | pip install insightface onnxruntime into ComfyUI's Python (Windows wheels: Gourieff/Assets) |
| insightface on CPU despite CUDA | only one of onnxruntime / onnxruntime-gpu |
| PuLID / InstantID / InfiniteYou find no face model | antelopev2 files directly in `insightface/models/antelopev2/` |
| PuLID-Flux influence persists after disconnecting; attn_mask dead | lldacing's pack, ApplyPulidFlux before cache patches |
| PuLID-Flux blurry backgrounds / crash | not fp8 e5m2; compute capability >= 8.0; fp16/bf16, e4m3fn or GGUF Q8 |
| weak likeness on a male face (PuLID) | raise weight / earlier start; or InstantID (SDXL) / InfiniteYou (FLUX) |
| InstantID burn / watermark / copied pose | CFG 4-5, 1016x1016, Advanced node noise ~0.35, image_kps |
| USO character fills the frame / loses features | 512 px reference (1024 for head-only) |

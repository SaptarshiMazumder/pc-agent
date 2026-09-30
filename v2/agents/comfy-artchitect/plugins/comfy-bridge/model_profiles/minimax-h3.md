```json
{
  "family": "MiniMax H3",
  "nodes": ["MiniMaxH3ReferenceToVideo", "MiniMaxH3ImageToVideo", "MiniMaxH3TextToVideo", "MiniMaxH3FirstLastFrameToVideo"],
  "prompt": {
    "input": "prompt",
    "required": ["subject_definitions:", "summary:", "retention_analysis:", "detailed_description:", "overall_soundscape:", "non_diegetic_music:"]
  },
  "weights": [
    {"slot": "reference-to-video model (Ref2VA)", "kind": "diffusion_models",
     "files": [
       {"file": "minimax_h3_ref2va_bf16.safetensors", "gb": 61.7, "min_vram": 80, "note": "full quality"},
       {"file": "minimax_h3_ref2va_pruned_bf16.safetensors", "gb": 37.5, "min_vram": 44, "note": "next best quality; wants 64 GB+ system memory"},
       {"file": "minimax_h3_ref2va_int8_convrot.safetensors", "gb": 31.7, "min_vram": 28, "note": "the usual quality pick"},
       {"file": "minimax_h3_ref2va_pruned_int8_convrot.safetensors", "gb": 19.5, "min_vram": 12, "note": "smallest; clear loss in quality"},
       {"file": "minimax_h3_ref2va_pruned_fp8_scaled.safetensors", "gb": 19.5, "min_vram": 12, "note": "as small, faster on RTX 40/50", "same_as": "minimax_h3_ref2va_pruned_int8_convrot.safetensors"}
     ]},
    {"slot": "text/image-to-video model (FL2VA)", "kind": "diffusion_models",
     "files": [
       {"file": "minimax_h3_fl2va_bf16.safetensors", "gb": 61.7, "min_vram": 80, "note": "full quality"},
       {"file": "minimax_h3_fl2va_pruned_bf16.safetensors", "gb": 37.5, "min_vram": 44, "note": "next best quality; wants 64 GB+ system memory"},
       {"file": "minimax_h3_fl2va_int8_convrot.safetensors", "gb": 31.7, "min_vram": 28, "note": "the usual quality pick"},
       {"file": "minimax_h3_fl2va_pruned_int8_convrot.safetensors", "gb": 19.5, "min_vram": 12, "note": "smallest; clear loss in quality"},
       {"file": "minimax_h3_fl2va_pruned_fp8_scaled.safetensors", "gb": 19.5, "min_vram": 12, "note": "as small, faster on RTX 40/50", "same_as": "minimax_h3_fl2va_pruned_int8_convrot.safetensors"}
     ]},
    {"slot": "text encoder (Qwen3-VL 32B)", "kind": "text_encoders",
     "files": [
       {"file": "qwen3vl_32b_minimax_h3_bf16.safetensors", "gb": 48.0, "min_vram": 64, "note": "full precision"},
       {"file": "qwen3vl_32b_minimax_h3_int8_convrot.safetensors", "gb": 25.3, "min_vram": 24, "note": "runs well on any modern card (ComfyUI unloads it after encoding)"},
       {"file": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", "gb": 14.6, "min_vram": 12, "note": "NVFP4: fast only on Blackwell cards, slower elsewhere",
        "best_on_gpu": ["RTX 50", "RTX PRO 6000", "Blackwell", "B100", "B200", "GB200"]}
     ]}
  ],
  "download": "https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/{kind}/{file}",
  "settings": [
    {"node": "MiniMaxH3ReferenceToVideo", "input": "ref_image_size", "value": "max", "why": "keeps up to 2048 px of each reference: the strongest likeness (match shrinks them to the output size)"},
    {"node": "BasicScheduler", "input": "scheduler", "value": "beta", "why": "the workflow's own notes: beta or normal outperform simple for reference-heavy prompts"},
    {"node": "ResolutionSelector", "input": "megapixels", "value": 0.98, "within": 0.05, "why": "1344x768 at 16:9, the model's native 768p; lower loses face detail"}
  ],
  "pitfalls": [
    "The prompt steers more than the references: pictures that do not match what the prompt describes are largely ignored.",
    "Every reference rides along every sampling step: more references and ref_image_size max are slower.",
    "Faces that must be kept need medium shots and close-ups; wide shots leave them a few pixels tall."
  ]
}
```

MINIMAX H3 PROMPT FORMAT — its official format (MiniMax calls it Context-IR). A plain paragraph gives clearly
weaker results, above all weaker likeness to reference pictures. Write ALL six sections, in this order, in
English (dialogue and lyrics keep their language):

subject_definitions:
  One line per thing to track. "<Subject 1> is the red-haired woman in <Picture 1>, with shoulder-length wavy
  auburn hair, green eyes and a freckled fair complexion." People, places, outfits and objects are <Subject N>;
  cite the <Picture N> they come from (pictures are numbered in the order they are wired). A picture used as an
  actual first/last frame is its own line: "<Picture 2> is the first frame of [Shot 1]."
summary:
  One paragraph starting with the task type in brackets: [reference generation] (pictures guide who/what),
  [keyframe completion] (a picture IS a frame), + [audio reference] / [audio reuse] when audio is given.
retention_analysis:
  One line per label: "<Subject 1> (appears in [Shot 1], [Shot 2]): fully_preserved - her face, eyes, hair
  colour and features are retained." Markers: fully_preserved, partially_preserved (say what changes, e.g. a
  new outfit), attribute_transfer, weak_reference. Identity you must keep = fully_preserved.
detailed_description:
  350-500 words. Open with one or two sentences of overall style. Then shot by shot: "[Shot 1] ..." (no time),
  "[Shot 2] At 00:02.500, ..." for each cut. Per shot: composition and shot size, each subject's look and
  position (name the label at its first appearance), action, environment and light, camera move, sound.
  Singers and speakers get stable IDs: "<Subject 1> (S1) sings, <d>[English] ...lyric...</d>".
overall_soundscape:
  Ambience and physical sound across the whole clip (crowd roar, room tone...).
non_diegetic_music:
  Music only the audience hears: instruments, tempo, build — or N/A.

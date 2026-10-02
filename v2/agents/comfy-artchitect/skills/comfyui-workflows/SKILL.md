---
name: comfyui-workflows
description: Use when designing, checking, running or repairing a ComfyUI workflow — where model knowledge comes from, how a stage is built, how to read the design check, and how to read what a machine rejected.
always: true
---

# Building a ComfyUI workflow that is right

## Where model knowledge comes from

**`kb_lookup` is the knowledge.** It was built from the publishers' own pages, model cards and
reference workflows, each fact with its source, and it holds only FREE models. Per family: every
file with its exact name, size, link and folder; the recipes (reference workflows that pass the
checks); what each parameter does; the RULES a correct graph obeys; prompting; pitfalls.
- `kb_lookup(task=…)` — the free models for a kind of job, best first, with what can rule each out.
- `kb_lookup(family=…)` — a family's recipes.
- `kb_lookup(family, recipe)` — what a stage built from it exposes, its files and its prompting guide.

Memory is not a source: families wire completely differently (a checkpoint with everything inside
vs a bare model with separate text encoders and VAE; cfg 7 vs cfg 1; 20 steps vs 4), and new
ones ship monthly. A model the knowledge base does not cover is researched at the source — the
publisher's reference workflow (`comfy_research` finds it; fetch the `.json`, never a weight
file), its model card, then two independent sources agreeing on the wiring — and built as a
custom stage.

## A stage

A stage is one workflow: a recipe with only what differs — `ports` (prompt, size, length, seed…)
and `inputs` (where each image, video or audio comes from):
- `user:<role>` — a file the person adds in a slot (`photo`, `garment`, `start_frame`);
- `stage:<earlier stage>.<output>` — what an earlier stage made; the run hands it over.

Split a job where a person would see a step: a character sheet before the shots that use it, a
keyframe before the video that starts from it. Put `review: true` on the last cheap step before
a slow one, so a wrong still is caught before minutes of video are spent on it.

**A custom stage** (no recipe) is a node list in API format plus its `outputs`:

```json
{
  "4": { "class_type": "CheckpointLoaderSimple", "inputs": { "ckpt_name": "sd_xl_base_1.0.safetensors" } },
  "3": { "class_type": "KSampler",
         "inputs": { "seed": 42, "steps": 20, "cfg": 8.0, "sampler_name": "euler", "scheduler": "normal",
                     "denoise": 1.0, "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0],
                     "latent_image": ["5", 0] } }
}
```

Keys are node ids as strings; an input is a literal or a link `[upstream_id, output_slot]`. A
loader reading a person's file uses the slot token `@<role>`. The UI format (`nodes[]`,
`links[]`, `widgets_values`) is written for you beside every workflow — never by hand, and a UI
file is never hand-converted into API format.

## Reading the design check

The report judges each stage in three layers, then the pipeline:
1. **Structure** — every node class exists, every link joins matching types, every value is in
   range and in its list, there is an output node. Checked against the node list last captured
   from the user's machine, otherwise the one shipped for the agent's ComfyUI version.
2. **The family's rules** — the facts that make a graph of THAT model correct: which encoder and
   VAE pair with which model, numeric windows (size multiples, frame counts, cfg, shift), what
   must sit upstream of what, the ComfyUI version it needs.
3. **The pipeline** — every input bound, stages in an order that can run, the media type an
   earlier stage makes is what the later one reads, disk, and versions.

`✗` is wrong: fix it. `?` is a judgement the check cannot make (a value only known when it runs,
a GPU not known yet): fix it, or say in one line why it is right. A rule finding names the family
and rule and says why the rule exists — read it before changing anything. The design is done when
it holds.

## Reading the machine

- `comfy_node_spec <class>` — one node's real inputs: `required` and `optional`, each
  `[type, config]`. A nested array type is an enum and lists the values this machine accepts.
- `comfy_node_search` — the real class names for a node, with `deprecated` flagged: the
  successor goes in the graph.
- `comfy_inventory` — the model files the machine has. It verifies a download landed; it never
  decides a design.

## When it is rejected

A run that fails comes back with `node_errors` keyed by node id:

| type | what it means | the fix |
|---|---|---|
| `value_not_in_list` | that name is not on this machine | a model the design needs is a missing file → `pipeline_provision`; otherwise the error lists the valid values |
| `missing_node_type` | that node's pack is not on this machine | first check the class NAME; a genuinely missing pack → `pipeline_provision` (or `comfy_node_install`) |
| `required_input_missing` | an input was left out | `comfy_node_spec` shows what is required |
| `return_type_mismatch` | a link joins incompatible outputs | check which output slot is linked |
| `bad_linked_input` | a link is not `[id, slot]` | fix the shape |
| `value_smaller_than_min` / `value_bigger_than_max` | out of range | the spec carries `min`/`max` |

A `TypeError` at execute time is a wrong input KEY: copy the keys the error shows. A 200 with
`node_errors` is a warning about a pruned branch, not a failure. A repair fixes values and
wiring; it never changes the model, its size or its node class — that is a design change.

## Timing

A first run on a cold model takes minutes; video longer. "still rendering" is normal: collect it
with another `pipeline_run` (or `comfy_run_status` for a template step). A timeout means still
running, not failed.

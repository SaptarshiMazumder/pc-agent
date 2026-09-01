---
name: comfyui-workflow-engineering
description: Use when the user asks to design, generate, validate, troubleshoot, or revise a ComfyUI workflow or choose models and custom nodes for one.
always: false
---

# ComfyUI workflow engineering

## Goal

Deliver a workflow the user can import into ComfyUI, plus an evidence-backed installation manifest. Build a useful first version without turning the request into an interview.

## 1. Establish the design envelope

Extract what the user supplied:

- task: text-to-image, image-to-image, inpaint, ControlNet, upscale, video, audio, 3D, or a composed pipeline;
- required model family or exact variant;
- quality, speed, resolution, duration, frame rate, and batch goals;
- GPU, VRAM, RAM, OS, and precision/offload constraints;
- inputs and desired outputs;
- whether only core nodes are allowed;
- installed ComfyUI path, version, models, and custom nodes, if known.

Ask one concise question only if a missing fact makes a valid graph impossible. Otherwise declare reasonable defaults and proceed.

If the user attaches an existing workflow, treat it as the baseline. Read it before proposing changes.

## 2. Research current components

For current availability, filenames, licenses, node support, or compatibility, use live web research. Search broadly, then verify and triangulate the implementation details. A new release is a research task—not an automatic limitation.

Use this discovery ladder until enough evidence exists to build the workflow:

1. Search the public web for the exact model name plus natural-language terms such as ComfyUI, workflow, example, nodes, loader, GGUF, FP8, quantized, or the requested task. Do not use guessed URLs or search operators.
2. Open the official model/project documentation, repository, releases, examples, issues, and discussions.
3. Search official ComfyUI documentation, example workflows, release notes, issues, pull requests, and upstream source for native or recently added support.
4. Inspect Hugging Face model cards, repository file trees, discussions, linked demos/Spaces, `README` usage, configuration files, and any workflow JSON or example scripts.
5. Inspect the exact Civitai model/version page, version metadata, creator notes, resource dependencies, example images, and downloadable or image-embedded workflow metadata when accessible.
6. Search the ComfyUI Manager registry and candidate custom-node repositories. Read their README, releases, node registration mappings, source definitions, examples, issues, and dependency files.
7. Search recent community examples, workflow-sharing sites, issue threads, forum/Reddit posts, and videos. Treat them as discovery evidence; cross-check critical node and file details against source code or a second independent example.
8. If a workflow PNG/WebP or JSON is available, download only the small example artifact, extract its embedded workflow metadata where possible, and inspect the actual node types, connections, model names, and widget values.
9. If implementation is still unclear and the user has local ComfyUI, inspect `/object_info`, installed custom-node source, and model directories read-only.

Do not stop after one empty source. Vary the source class and query. A Civitai login wall does not end the search: continue through the creator's Hugging Face/GitHub pages, ComfyUI examples, custom-node source, and public community artifacts.

For every external component, record:

- human name and exact variant;
- exact repository/page URL that was actually opened;
- exact filename(s), when verified;
- expected ComfyUI folder (`models/checkpoints`, `models/diffusion_models`, `models/text_encoders`, `models/vae`, `models/loras`, `models/controlnet`, `models/upscale_models`, or the documented custom location);
- base architecture and component role;
- required companion encoders, VAE, CLIP/LLM, scheduler, or motion model;
- license or gated-access caveats;
- quantization/precision and approximate hardware implications;
- custom-node repository and any version/commit requirement;
- confidence: confirmed, source-conflicting, or unverified.

Do not infer filenames from a model title. Do not convert a page slug into a guessed URL. If Civitai or another site is login-gated or blocked, record that source as inaccessible and continue the discovery ladder. Report the gap only after other likely sources have been checked.

## 3. Select an architecture

Choose the smallest reliable graph that satisfies the request. Prefer core ComfyUI nodes when they correctly support the model. Add a custom-node pack only when it provides a required loader, sampler, conditioning path, video operation, or material usability benefit.

Check architectural compatibility explicitly:

- checkpoint versus separate diffusion-model format;
- text encoder family and count;
- VAE/latent format;
- prediction type and model sampling configuration;
- scheduler and sampler support;
- guidance semantics, including distilled/turbo models;
- resolution and frame-count constraints;
- LoRA base-model compatibility;
- ControlNet/adapter base-model compatibility;
- video latent, FPS, frame interpolation, decoding, and output container requirements.

Never treat model families as interchangeable merely because they solve the same task.

## 4. Obtain node interfaces

A node title is not an interface specification. Before writing a graph, obtain node definitions from one or more of:

1. the user's running ComfyUI `/object_info` response;
2. installed or online ComfyUI/custom-node source, including node registration mappings and input/output definitions;
3. an official or creator-provided example workflow using the same node version;
4. workflow metadata extracted from a published example image;
5. authoritative source/docs that enumerate inputs, outputs, and widgets;
6. multiple recent community workflows whose serialized interfaces agree.

If the first source is incomplete, continue through the others. When exact support still cannot be proven after the discovery ladder:

- use verified native/core nodes if the model architecture is demonstrably compatible;
- otherwise produce a clearly marked experimental draft only when there is strong, cross-checked evidence, enumerate every assumed interface, and do not call it ready;
- prefer asking the user for one high-value artifact—an example workflow/image, local `/object_info`, or their custom-node folder—rather than broadly asking them to research the model;
- state what sources were attempted and exactly what evidence is still missing.

When a local ComfyUI URL or installation path is available, inspect it read-only. Ask before installing nodes, downloading models, starting services, or queuing execution.

## 5. Build the UI workflow

Unless API format is explicitly requested, create a ComfyUI UI workflow JSON with the normal graph fields, including:

- `last_node_id`, `last_link_id`, `nodes`, `links`, `groups`, `config`, `extra`, and `version`;
- unique numeric node IDs;
- unique numeric link IDs;
- node `type`, position, size, flags/order/mode, inputs, outputs, properties, and widget values appropriate to the verified node interface;
- links in the top-level form `[link_id, origin_node_id, origin_slot, target_node_id, target_slot, type]`;
- matching input link references and output link lists;
- readable left-to-right layout;
- labels, groups, and notes for model loading, prompting/conditioning, sampling, decoding, and output.

Preserve unknown fields when revising a user-supplied workflow. Do not normalize away extension metadata merely because it is not understood.

If API prompt JSON is requested, produce the node-ID mapping with `class_type` and `inputs`. If both formats are useful, write separate clearly named files.

## 6. Validate in layers

Run all validation that is available and report each layer separately.

### Layer A — JSON and graph structure

- parses as JSON;
- expected top-level workflow shape exists;
- node and link IDs are unique;
- `last_node_id` and `last_link_id` cover all IDs;
- every link endpoint references an existing node;
- input link references and output link lists agree with top-level links;
- origin and target slots exist in the serialized node records;
- no impossible dangling connection is presented as valid.

Use the private `validate_comfyui_workflow` tool for this layer when available.

### Layer B — node and model evidence

- node classes and ports match a verified interface source;
- widget values match the expected node order or named API inputs;
- model component combinations are compatible;
- exact required files and their destinations are documented;
- source URLs were actually opened and are included in the manifest.

### Layer C — local ComfyUI compatibility

When `/object_info` or an installation is available, compare every node type and required input against that exact installation. Report missing node classes and models.

### Layer D — execution

Only call the workflow execution-tested if it was actually queued and completed in ComfyUI. Capture the run result or error. Structural validation is not execution.

## 7. Deliver artifacts

Write all deliverables into the current agent workspace under one revisioned project folder so the app's Artifacts tab can enumerate them:

- `workflows/<project>/workflow-v001.json`
- `workflows/<project>/workflow-v001-api.json`
- `workflows/<project>/manifest-v001.md`

Use these relative paths directly with `write`; do not create duplicate `data/` or `docs/` copies. Before telling the user an artifact is available, use `read`, `ls`, or `find` to confirm the exact file exists in this agent's workspace.

The manifest must include:

- workflow purpose and assumptions;
- tested/validated status by layer;
- model table with source URLs, filenames, and target folders;
- custom-node table with repository URLs and version notes;
- installation order;
- recommended defaults and tunable controls;
- VRAM/performance notes;
- unresolved uncertainties;
- import and run instructions.

In chat, link or name the artifact paths and summarize the design. Do not paste a huge JSON blob unless the user asks.

## 8. Install missing dependencies

When a test run reveals missing models or custom nodes, resolve them automatically — but always with the user's approval for every install action.

### Custom nodes via ComfyUI Manager

If the target ComfyUI has ComfyUI Manager installed (it exposes `/customnode/install` and related endpoints):

1. Call `/customnode/getlist` to search for the missing node pack by name or GitHub URL.
2. Present the exact pack name, repository URL, description, and any noted risks to the user.
3. After approval, POST to `/customnode/install` with the pack reference.
4. Poll `/customnode/install/progress` until complete, then verify the node appears in `/object_info`.
5. If the Manager route returns a failure, read the error and try the direct git approach or report the blocker.

If ComfyUI Manager is NOT installed, use the browser to open the Manager's web UI at the ComfyUI URL, search for the pack, and guide the user to click Install. Alternatively, if the user permits, clone the repository directly into the `custom_nodes/` directory.

### Models

1. Identify the exact download URL from Hugging Face, Civitai, or the official source.
2. Compute or verify the target folder from the node's model-type expectation.
3. Before downloading multi-gigabyte weights, tell the user the filename, size, source URL, and destination, and ask for approval.
4. If the ComfyUI host has a direct-download API (ComfyUI Manager's model installer or the host's own endpoint), prefer that. Otherwise use `wget` / `curl` / Python with a resume-capable download into the correct folder.
5. After download, verify the file exists at the expected path with the expected size, then re-submit the workflow.

### Approval rule

Never install a custom node or download a model without explicit per-action approval. Say what will be installed, from where, where it will go, and any known risks (third-party code execution, size, license, gated access). Wait for the user's yes before proceeding.

## 9. Iterate safely

When the user requests a change such as FLUX to Z-Image, Z-Image to Z-Image Turbo, adding a video stage, changing a denoiser, or swapping a scheduler:

1. read the latest workflow and manifest;
2. classify the change as parameter-only, component substitution, or architecture change;
3. research the replacement and its compatibility;
4. preserve unrelated nodes, positions, groups, notes, and settings;
5. write the next revision rather than overwriting the previous one;
6. rerun all applicable validation layers;
7. give a concise delta: added, removed, rewired, changed defaults, new downloads, and new risks.

A family swap often changes loaders, encoders, latent format, guidance, sampler settings, and VAE. Rebuild that subgraph when required instead of relabeling the old model loader.

## 10. Boundaries

- Do not download multi-gigabyte weights unless the user explicitly approves the exact files and destination.
- Do not install or update custom nodes without approval.
- Warn that custom nodes execute third-party code; prefer reviewed, maintained repositories.
- Respect gated models and licenses. Never bypass access controls.
- For obscure or newly released models, exhaust practical web, repository, workflow-artifact, and source-code discovery first. Ask the user for an example workflow/image or local `/object_info` only when that would resolve a specific remaining gap.

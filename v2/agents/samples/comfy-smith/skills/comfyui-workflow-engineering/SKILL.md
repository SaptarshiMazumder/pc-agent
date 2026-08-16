---
name: comfyui-workflow-engineering
description: Use when building, running or revising a ComfyUI workflow on a remote server — reading what that server has, wiring the graph against it, running it, and fixing what fails.
---

# Building a ComfyUI workflow on a remote server

The output is a file that RUNS on a machine you cannot see. Everything here exists to make that
file work on THAT server, not to describe what a working file would look like.

## The order of work

1. `comfy_server` — is it up, what GPU, how much VRAM is free
2. `list_workflows` — have you built this before
3. `comfy_models` / `comfy_nodes` — what is installed, exact names
4. research (only for what the server cannot answer)
5. design, write to `workspace/workflows/`
6. `validate_workflow`
7. **`run_workflow`** — and read the real failure
8. fix, run again, until it produces an image
9. hand over

Steps 1–3 replace almost every question you would otherwise ask the user. Step 7 is the one that
separates a workflow that works from one that sounds like it does.

## 1. Ask the server what it is

**`comfy_server` first, every time.** Reachability, GPU, **free** VRAM, ComfyUI version, queue
depth, and whether SSH is available.

Free VRAM is the number that matters, not the card's advertised size — a server with a model
already resident has far less than the box says, and "24GB card" plus "3GB free" is a workflow
that dies at the first sampler. Never ask the user for VRAM. They will tell you what they bought.

If `COMFY_URL` is unset, the tool says so and names the setting. Relay that in one line and stop.
Do not design against an imaginary machine.

**`list_workflows` before building anything "like last time".** Revising a graph they approved
keeps the sampler, resolution and seed they tuned; rebuilding discards all of it.

## 2. Ask the server what it has

**`comfy_models`** for weights. The names it returns are the exact strings a loader node accepts
— including the extension and any version suffix. A checkpoint name that is off by `_1.0` is
rejected at queue time.

**`comfy_nodes`** for node classes. Two uses, both mandatory:

- `search` to find out whether this server has the kind of node you want at all
- `node="<ClassName>"` before you write that node, for its exact required inputs, their types and
  their allowed values

**Never write a `class_type` you have not looked up on this server.** Node names from memory are
guesses; a large share of useful nodes come from custom packs that this particular box may not
have installed. This is the single most common way a generated workflow fails.

## 3. Research only what the server cannot tell you

The server knows what it HAS. It does not know what is good, what a new model is called, or what
settings a checkpoint wants. That is what `web_search`, `web_fetch` and `browser` are for.

**Comparing several candidates? Delegate them.** `spawn_subagent` gives each its own context:

> "Read the model card for <model> and report: exact filename, file size, the folder it belongs
> in under ComfyUI/models/, minimum VRAM, and any custom node required. Say which of those you
> could not confirm."

Three run at once and come back as three short answers instead of pulling pages of documentation
into this conversation. Give each a COMPLETE task — the child cannot see this conversation.

**Use `browser` when `web_fetch` comes back thin.** Civitai and HuggingFace render details with
JavaScript, so a plain fetch returns a shell with no filename and no size. That is almost always
the cause of "I could not confirm the download".

- **Never write a filename or URL you did not see.**
- **Separate confirmed from assumed.** "confirmed from the model card" and "usually goes in" are
  different claims and the user is acting on both.

## 4. Design the graph

Work backwards from the output: what saves the image → what decodes it → what samples → what
conditions it → what loads the model. Every input must come from a node you have already placed.

Prefer the smallest graph that does the job. Extra nodes are extra ways to be wrong.

**Fit it to the free VRAM you measured**, not to the card's nominal size — precision, resolution,
batch size and tiling all follow from that one number.

## 5. Write it in API format

Under **the workspace this run reports**, in `workflows/`, as
`{"3": {"class_type": ..., "inputs": {...}}, ...}`.

**Do not work out that path yourself.** There is no fixed location for the workspace — a
signed-in user has their own, a project chat uses the project's — so a path built from the
agent's own folder is real, writable, and read by nothing. `list_workflows` prints the folder it
reads; write there. A file saved anywhere else is invisible to it and to the Workflows tab, and
looks exactly like a file that was never written.

**The API format is the one that runs.** `/prompt` does not accept a canvas export. If the user
also wants to open it in the ComfyUI canvas, write the UI export as a second file and say which
is which — but run the API one.

Every graph needs at least one output node (`SaveImage` and friends) or it produces nothing and
reports no error.

## 6. Validate

```
validate_workflow(path="…/workspace/workflows/<name>.json")
```

Structure plus, when the server is reachable, a live check: every class installed, every required
input present, every model and sampler name a value that server accepts. Fix every `[x]` and run
it again until clean.

Read what it says about the live half. If it reports that it could NOT reach the server, you have
checked the shape and nothing else — say that, and do not proceed to step 8 pretending otherwise.

## 7. RUN IT

```
run_workflow(path="…/workspace/workflows/<name>.json")
```

**This is not optional and it is not something you offer to do afterwards.** A workflow that
validates can still fail on a missing LoRA, a resolution that does not fit, or a sampler this
build renamed. Only the server settles it.

When it fails, it hands back ComfyUI's own error, naming the node and the input. That is the most
useful thing in the conversation:

- **"not in list"** → the name is wrong. Re-check with `comfy_models` and use the exact string.
- **"missing node type"** → not installed here. Offer to install it over SSH, or pick a node this
  server has.
- **out of memory** → drop resolution/batch, use a smaller model or lower precision, or add
  tiling. Say what you traded away.
- **a node's own exception** → read the traceback tail; it usually names the bad input.

Fix the specific thing and run it again. Do not rewrite the whole graph in response to one error.

Then look at what came back. The images land in `workspace/outputs/` and are attached to your
reply — if the result does not match what was asked for, that is also a failure, and it is one
only you can catch.

## 8. When the server is missing something

`comfy_server` reports the SSH target if the user configured one. With it:

```
exec: ssh <target> "cd /workspace/ComfyUI/custom_nodes && git clone <repo> && pip install -r <repo>/requirements.txt"
exec: ssh <target> "wget -O /workspace/ComfyUI/models/checkpoints/<file> <url>"
```

Then `comfy_nodes(refresh=true)`, because the catalogue you loaded is now stale. Custom nodes
need a ComfyUI restart to appear — say so rather than looping on a refresh that cannot work yet.

Without SSH, tell them exactly what to install and where, in one line. **Never silently swap in a
different model** and present the output as what they asked for.

## 9. Hand it over

In this order:

1. what it does, in one line
2. **that it ran, on which server, and what it produced**
3. any custom nodes or models you installed
4. what you assumed and could not verify

## Revising

**Edit the existing workflow.** Keep their resolution, sampler, seed and prompt unless the change
requires replacing them — and if it does, say which choice you discarded and why.

Re-validate AND re-run after every revision. A change that breaks a link is invisible until it
runs, and "I only changed one number" is how a working graph quietly stops working.

## Images the user gives you

An image in the chat is on THIS machine; the server cannot see it. `upload_input_image` puts it
in the server's input folder and returns the exact name a `LoadImage` node needs. That is the
first step of every img2img, inpainting or ControlNet job — and when someone shows you a bad
render and asks why, look at it before theorising.

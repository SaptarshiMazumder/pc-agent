# Clip assembly

The last stage of a multi-clip job: the clips earlier stages made, joined end to end (`join-N`, `join-N-audio` to keep sound) or stacked side by side (`stack-N`). Core ComfyUI nodes, no model, nothing to download. Bind each `clip_i` to `stage:<that stage>.video`, in the order they play.

- Every clip at the same frame size (join) or the same frame count (stack).
- A hard join only: a smooth move from one shot to the next is a first-last-frame stage, not this.
- Sources: Comfy-Org templates `templates-6-key-frames` and `template_rob_split_stack_qwen_multi_wan22`.

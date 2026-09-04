/* Thread — the conversation transcript. Each export is one card cell.
 *
 * Content mirrors what this agent actually does (ComfyUI workflow building), because these
 * cards are browsed by humans and imitated by the design agent — `foo` teaches it nothing.
 */
import { Thread } from 'agent-app'

const t = (mins: number) => Date.now() - mins * 60_000

export const Conversation = () => (
  <Thread
    running={false}
    items={[
      {
        kind: 'user',
        files: [],
        text: 'Build a text-to-image workflow using what my instance already has',
        ts: t(9),
      },
      {
        kind: 'think',
        text: 'The instance lists an SDXL base checkpoint and two LoRAs. No refiner — a single-pass graph fits.',
        streaming: false,
        ts: t(8),
      },
      {
        kind: 'tool',
        id: 'c1',
        name: 'comfy_probe',
        args: {},
        result: 'ComfyUI 0.3.99 (python 3.12, torch 2.5.1)\nNVIDIA RTX 4090: 21 GB free of 24 GB\nqueue: 0 waiting',
        done: true,
        isError: false,
        ts: t(7),
      },
      {
        kind: 'bot',
        text: 'Your instance is reachable — ComfyUI 0.3.99 on a 4090 with 21 GB free. It has `sd_xl_base_1.0.safetensors` plus two LoRAs. Plan: the classic SDXL backbone at 1024×1024, 25 steps. Want the LoRA in the first pass, or keep it plain?',
        streaming: false,
        ts: t(6),
      },
    ]}
  />
)

export const StreamingRun = () => (
  <Thread
    running={true}
    items={[
      { kind: 'user', files: [], text: 'Make it faster without changing the look', ts: t(2) },
      {
        kind: 'think',
        text: 'Fewer steps first — 25 → 18 keeps this checkpoint stable. Resolution stays.',
        streaming: true,
        ts: t(1),
      },
    ]}
  />
)

export const WithArtifacts = () => (
  <Thread
    running={false}
    items={[
      {
        kind: 'bot',
        text: 'Ran clean — both files are ready. The API file is what the server runs; drag the other into ComfyUI to see the graph.',
        streaming: false,
        ts: t(1),
        artifacts: [
          {
            path: 'workflows/flux-portrait.api.json',
            name: 'flux-portrait.api.json',
            mime: 'application/json',
            kind: 'file',
            size: 2843,
          },
          {
            path: 'workflows/flux-portrait.json',
            name: 'flux-portrait.json',
            mime: 'application/json',
            kind: 'file',
            size: 6120,
          },
        ],
      },
    ]}
  />
)

export const ErrorAndSystem = () => (
  <Thread
    running={false}
    items={[
      {
        kind: 'tool',
        id: 'c2',
        name: 'comfy_run',
        args: { workflow_path: 'workflows/flux-portrait.api.json' },
        result: "400: node 3 (KSampler): value_not_in_list — sampler_name 'euler_b' not in ['euler', 'dpmpp_2m', 'ddim']",
        done: true,
        isError: true,
        ts: t(3),
      },
      {
        kind: 'system',
        tone: 'error',
        text: 'This run ended while the window was away — the conversation up to here is saved.',
        ts: t(2),
      },
    ]}
  />
)

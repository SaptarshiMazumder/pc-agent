/* MessageItem — one transcript row, per kind. Thread composes these; solo cells make each
 * kind's chrome gradeable on its own. */
import { MessageItem } from 'agent-app'

export const UserBubble = () => (
  <MessageItem
    running={false}
    item={{
      kind: 'user',
      files: [],
      text: 'Connect to my ComfyUI and tell me what you can reach.',
      ts: Date.now() - 300_000,
    }}
  />
)

export const BotAnswer = () => (
  <MessageItem
    running={false}
    item={{
      kind: 'bot',
      streaming: false,
      text: 'Reached it — **ComfyUI 0.3.99** on an RTX 4090, 21 GB free. `comfy_inventory` lists 3 checkpoints and 2 LoRAs. What should we build?',
      ts: Date.now() - 240_000,
    }}
  />
)

export const ToolCall = () => (
  <MessageItem
    running={false}
    item={{
      kind: 'tool',
      id: 't1',
      name: 'comfy_upload',
      args: { paths: ['uploads/a1b2-face.png'] },
      result: 'uploads/a1b2-face.png  ->  face.png\nUse the RIGHT-hand names in LoadImage nodes.',
      done: true,
      isError: false,
      ts: Date.now() - 180_000,
    }}
  />
)

export const Subagent = () => (
  <MessageItem
    running={false}
    item={{
      kind: 'subagent',
      agent: 'researcher',
      steps: ['comfy_research("qwen image")', 'fetched the reference workflow', 'checked 6 names against the instance'],
      status: 'done',
      detail: 'Qwen-Image needs UNETLoader + qwen_2.5_vl encoder — encoder missing on your instance.',
      ts: Date.now() - 120_000,
    }}
  />
)

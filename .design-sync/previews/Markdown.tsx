/* Markdown — the renderer every bot message goes through. Text-heavy on purpose: this cell is
 * where a typography problem would show first. */
import { Markdown } from 'agent-app'

const DOC = `## The plan

Your instance has **SDXL base** plus two LoRAs. The graph, one node per line:

1. \`CheckpointLoaderSimple\` — \`sd_xl_base_1.0.safetensors\`
2. \`CLIPTextEncode\` ×2 — positive and negative
3. \`KSampler\` — 25 steps, cfg 7, \`dpmpp_2m\`
4. \`VAEDecode\` → \`SaveImage\`

| choice | why |
|---|---|
| 1024×1024 | SDXL's native training size |
| no refiner | you said fast |

> A workflow that has not run is not finished — I'll submit it before calling it done.

\`\`\`json
{ "4": { "class_type": "CheckpointLoaderSimple",
         "inputs": { "ckpt_name": "sd_xl_base_1.0.safetensors" } } }
\`\`\``

export const RichText = () => <Markdown text={DOC} />

export const InlineOnly = () => (
  <Markdown text={'Swapped `euler_b` for `dpmpp_2m` — the server listed it as legal. One change, so the result is attributable.'} />
)

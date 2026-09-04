/* WorkflowShelf — every workflow the agent emitted, one card per workflow with its two files.
 * The empty state ships too: it is the screen every fresh install sees first. */
import { WorkflowShelf } from 'agent-app'

const wf = (base: string, apiSize: number, uiSize: number | null) => {
  const files = [
    {
      path: `workflows/${base}.api.json`,
      name: `${base}.api.json`,
      mime: 'application/json',
      kind: 'file',
      size: apiSize,
    },
  ]
  if (uiSize) {
    files.push({
      path: `workflows/${base}.json`,
      name: `${base}.json`,
      mime: 'application/json',
      kind: 'file',
      size: uiSize,
    })
  }
  return files
}

export const Shelf = () => (
  <WorkflowShelf
    artifacts={[
      ...wf('sdxl-portrait', 2843, 6120),
      ...wf('flux-schnell-poster', 3390, 7404),
      ...wf('img2img-restyle', 2511, null),
    ]}
  />
)

export const Empty = () => <WorkflowShelf artifacts={[]} />

/* ArtifactView — files a turn produced, under the answer that produced them. File cards only:
 * an image artifact would point at the daemon's /file endpoint, which does not exist on a
 * static preview page (a broken-image icon teaches nothing). */
import { ArtifactView } from 'agent-app'

export const TwoFiles = () => (
  <ArtifactView
    artifacts={[
      {
        path: 'workflows/sdxl-portrait.api.json',
        name: 'sdxl-portrait.api.json',
        mime: 'application/json',
        kind: 'file',
        size: 2843,
      },
      {
        path: 'workflows/sdxl-portrait.json',
        name: 'sdxl-portrait.json',
        mime: 'application/json',
        kind: 'file',
        size: 6120,
      },
    ]}
  />
)

export const SingleFile = () => (
  <ArtifactView
    artifacts={[
      {
        path: 'notes/model-requirements.md',
        name: 'model-requirements.md',
        mime: 'text/markdown',
        kind: 'file',
        size: 1310,
      },
    ]}
  />
)

/* OutputsGrid — the Workspace's Outputs section: what this chat has made, as a wall of tiles
 * (newest first), each with its kind tag and filename; the file open in the viewer is ringed.
 *
 * Files are this chat's Artifacts at `outputs/<chat>/…`. Image tiles draw the daemon's
 * `/thumbnail`; with no daemon here OutputThumb falls back to its own placeholder glyph — the
 * component's real offline face, not a mock. Cells sweep: the full-width wall with one tile open
 * in the viewer, the same wall narrowed to the column beside the viewer (the `.ws.is-narrow`
 * parent the dashboard sets, which drops captions and tags), and the empty section.
 * The per-tile Download / Add to Library / Delete row shows on hover only, and Delete's
 * confirmation opens on a click — neither is reachable statically. */
import { OutputsGrid } from 'agent-app'

const CHAT = 'chat-1728391205-k3v9'
const WS = '/srv/agentd/workspace'
const now = Math.floor(Date.now() / 1000)

const out = (name: string, kind: 'image' | 'audio', mime: string, size: number, ago: number) => ({
  path: `${WS}/outputs/${CHAT}/${name}`,
  name,
  mime,
  kind,
  size,
  modified: now - ago,
})

const OUTPUTS = [
  out('person_still_00001.png', 'image', 'image/png', 2_310_000, 1800),
  out('person_still_00002.png', 'image', 'image/png', 2_280_000, 900),
  out('person_still_00003.png', 'image', 'image/png', 2_340_000, 900),
  out('voice_clean_00001.flac', 'audio', 'audio/flac', 1_120_000, 700),
  out('person_still_upscale_00001.png', 'image', 'image/png', 6_870_000, 400),
]

const noop = () => {}
const save = async () => ({ ok: true, message: 'Added to your Library.' })

export const RendersOneOpen = () => (
  <div style={{ maxWidth: 640 }}>
    <OutputsGrid
      outputs={OUTPUTS}
      selectedPath={OUTPUTS[1].path}
      onOpen={noop}
      onDelete={async () => {}}
      onAddToLibrary={save as never}
    />
  </div>
)

export const BesideTheViewer = () => (
  <div className="ws is-narrow" style={{ padding: 12 }}>
    <OutputsGrid
      outputs={OUTPUTS}
      selectedPath={OUTPUTS[4].path}
      onOpen={noop}
      onDelete={async () => {}}
      onAddToLibrary={save as never}
    />
  </div>
)

export const NothingRenderedYet = () => (
  <div style={{ maxWidth: 640 }}>
    <OutputsGrid outputs={[]} selectedPath="" onOpen={noop} />
  </div>
)

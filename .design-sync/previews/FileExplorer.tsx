/* FileExplorer — the studio's "All files" tree: everything one chat's agent MADE, as the
 * workspace folders it actually is (workflows/, outputs/, plus any folder a tool wrote to).
 * The chat's own folder segment is dropped from the display path; folders start open with a
 * file count; names wrap instead of truncating, so a workflow's pair (`x.api.json` to run,
 * `x.json` to import) reads as two files. The row of the file open in the centre pane is
 * highlighted (`selected`, owned by the parent).
 *
 * Cells: a populated tree; the same tree with a file selected; a fresh chat with nothing made
 * (the empty sentence). Ticking files for deletion (the checkbox) is internal state that a
 * click sets, so the selection bar (Add to Library / Delete / Clear) is not shown here; its
 * confirm is DeleteFilePrompt's own card. Pure props — no daemon call. */
import { FileExplorer } from 'agent-app'

const CHAT = 'chat-1728391205-k3v9'
const WS = '/srv/agentd/workspace'
const now = Math.floor(Date.now() / 1000)

const art = (dir: string, name: string, kind: 'image' | 'video' | 'audio' | 'file', mime: string, size: number, age: number) => ({
  path: `${WS}/${dir}/${CHAT}/${name}`,
  name,
  mime,
  kind,
  size,
  modified: now - age,
})

const MADE = [
  art('workflows', 'talking_portrait.api.json', 'file', 'application/json', 18_400, 2400),
  art('workflows', 'talking_portrait.json', 'file', 'application/json', 41_900, 2400),
  art('workflows', 'person_still_flux.api.json', 'file', 'application/json', 9_800, 3600),
  art('workflows', 'person_still_flux.json', 'file', 'application/json', 22_300, 3600),
  art('outputs', 'person_still_00001.png', 'image', 'image/png', 2_310_000, 1800),
  art('outputs', 'person_still_00002.png', 'image', 'image/png', 2_280_000, 900),
  art('outputs', 'person_still_00003.png', 'image', 'image/png', 2_340_000, 900),
  art('outputs', 'talking_clip_00001.mp4', 'video', 'video/mp4', 6_720_000, 300),
  art('outputs', 'voice_line_trimmed.wav', 'audio', 'audio/wav', 880_000, 600),
  {
    path: `${WS}/installers/install_wan22_s2v.sh`,
    name: 'install_wan22_s2v.sh',
    mime: 'text/x-shellscript',
    kind: 'file' as const,
    size: 2_150,
    modified: now - 4200,
  },
]

const noop = () => {}

const Rail = ({ children }: { children: React.ReactNode }) => <div style={{ width: 320 }}>{children}</div>

export const Populated = () => (
  <Rail>
    <FileExplorer artifacts={MADE} onSelect={noop} onDelete={async () => {}} onAddToLibrary={async () => ({ message: '' }) as any} />
  </Rail>
)

export const FileOpen = () => (
  <Rail>
    <FileExplorer
      artifacts={MADE}
      selected={MADE[1]}
      onSelect={noop}
      onDelete={async () => {}}
      onAddToLibrary={async () => ({ message: '' }) as any}
    />
  </Rail>
)

export const NothingMadeYet = () => (
  <Rail>
    <FileExplorer artifacts={[]} onSelect={noop} />
  </Rail>
)

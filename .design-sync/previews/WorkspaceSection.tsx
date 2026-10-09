/* WorkspaceSection — one foldable section of the Workspace (Inputs, Stages, Outputs, Workflow,
 * All files): a head with a caret, the title, a count and — when something in it waits on the
 * person — a "waiting on you" flag; the body is whatever the dashboard puts there.
 *
 * Each cell gives it the real child the dashboard gives it, inside the `.ws` column it lives in:
 * Inputs waiting on a voice track (attention, "1 of 2", ReferenceSlots), Outputs open with this
 * chat's renders (OutputsGrid), and the Workflow section open (WorkflowPanel) above All files
 * folded (the dashboard's defaultOpen={false}, FileExplorer inside). Image tiles fall back to
 * OutputThumb's placeholder glyph offline (no daemon `/thumbnail`). */
import { FileExplorer, OutputsGrid, ReferenceSlots, WorkflowPanel, WorkspaceSection } from 'agent-app'

const CHAT = 'chat-1728391205-k3v9'
const WS = '/srv/agentd/workspace'
const now = Math.floor(Date.now() / 1000)

const file = (dir: string, name: string, kind: 'image' | 'audio', mime: string, size: number, ago: number) => ({
  path: `${WS}/${dir}/${CHAT}/${name}`,
  name,
  mime,
  kind,
  size,
  modified: now - ago,
})

const FACE = file('references', 'face.jpg', 'image', 'image/jpeg', 412_000, 1200)
const OUTPUTS = [
  file('outputs', 'person_still_00001.png', 'image', 'image/png', 2_310_000, 1800),
  file('outputs', 'person_still_00002.png', 'image', 'image/png', 2_280_000, 900),
  file('outputs', 'person_still_00003.png', 'image', 'image/png', 2_340_000, 900),
]

const wfFile = (name: string, size: number) => ({
  path: `${WS}/workflows/${CHAT}/${name}`,
  name,
  mime: 'application/json',
  kind: 'file' as const,
  size,
  modified: now - 1300,
})
const WORKFLOWS = [wfFile('person_still.api.json', 6_412), wfFile('person_still.json', 14_906)]

const noop = () => {}
const Column = ({ children }: { children: React.ReactNode }) => (
  <div className="ws" style={{ maxWidth: 640 }}>
    {children}
  </div>
)

export const InputsWaitingOnYou = () => (
  <Column>
    <WorkspaceSection title="Inputs" count="1 of 2" attention>
      <ReferenceSlots
        slots={[
          { role: 'face', what: 'a clear, front-facing photo of the person', workflows: ['person_still'], file: FACE, fedBy: null },
          { role: 'voice', what: 'the line they say, a WAV or MP3 under 30 seconds', workflows: ['talking_clip'], file: null, fedBy: null },
        ]}
        free={[]}
        disabled={false}
        onAdd={async () => {}}
        onOpen={noop}
        onFromLibrary={noop}
      />
    </WorkspaceSection>
  </Column>
)

export const OutputsOpen = () => (
  <Column>
    <WorkspaceSection title="Outputs" count={String(OUTPUTS.length)}>
      <OutputsGrid outputs={OUTPUTS} selectedPath="" onOpen={noop} onDelete={async () => {}} />
    </WorkspaceSection>
  </Column>
)

export const WorkflowOpenAllFilesFolded = () => (
  <Column>
    <WorkspaceSection title="Workflow" count="1">
      <WorkflowPanel files={WORKFLOWS} onOpen={noop} onSaveTemplate={noop} />
    </WorkspaceSection>
    <WorkspaceSection title="All files" count={String(OUTPUTS.length + WORKFLOWS.length)} defaultOpen={false}>
      <FileExplorer artifacts={[...WORKFLOWS, ...OUTPUTS]} onSelect={noop} />
    </WorkspaceSection>
  </Column>
)

/* WorkflowPanel — the Workspace's Workflow section: every workflow this chat emitted (each role's
 * `<name>.api.json` run file and `<name>.json` ComfyUI file paired into one card), Save to Library
 * and View graph under each, the portable installer block when a passing validate exported one,
 * and "Save as template to reuse" for the whole chat.
 *
 * Files are this chat's workflow-folder Artifacts at `workflows/<chat>/…`. Cells sweep: two
 * workflows with every action and the chat's template button, one workflow with the installer a
 * passing validate exported (install_<role>.py + manifest), one workflow while a turn is running
 * (delete refused, with the reason the window gives), and the empty section. The delete
 * confirmation and the save feedback open on a click — not reachable statically. */
import { WorkflowPanel } from 'agent-app'

const CHAT = 'chat-1728391205-k3v9'
const WS = '/srv/agentd/workspace'
const now = Math.floor(Date.now() / 1000)

const wfFile = (name: string, mime: string, size: number, ago: number) => ({
  path: `${WS}/workflows/${CHAT}/${name}`,
  name,
  mime,
  kind: 'file' as const,
  size,
  modified: now - ago,
})

const FILES = [
  wfFile('person_still.api.json', 'application/json', 6_412, 2100),
  wfFile('person_still.json', 'application/json', 14_906, 2100),
  wfFile('install_person_still.py', 'text/x-python', 9_870, 1900),
  wfFile('install_person_still.manifest.json', 'application/json', 2_214, 1900),
  wfFile('talking_clip.api.json', 'application/json', 9_133, 1300),
  wfFile('talking_clip.json', 'application/json', 21_480, 1300),
]

const noop = () => {}
const save = async () => ({ ok: true, message: 'Added to your Library.' })
const Panel = ({ children }: { children: React.ReactNode }) => <div style={{ maxWidth: 600 }}>{children}</div>

export const TwoWorkflows = () => (
  <Panel>
    <WorkflowPanel
      files={FILES.filter((f) => !f.name.startsWith('install_'))}
      onDelete={async () => {}}
      onAddToLibrary={save as never}
      onSaveTemplate={noop}
      onOpen={noop}
    />
  </Panel>
)

export const WithItsInstaller = () => (
  <Panel>
    <WorkflowPanel files={FILES.slice(0, 4)} onDelete={async () => {}} onAddToLibrary={save as never} onOpen={noop} />
  </Panel>
)

export const WhileATurnRuns = () => (
  <Panel>
    <WorkflowPanel
      files={FILES.slice(4)}
      onDelete={async () => {}}
      onAddToLibrary={save as never}
      onSaveTemplate={noop}
      onOpen={noop}
      deletionDisabled="Wait for the current turn to finish"
    />
  </Panel>
)

export const NoWorkflowYet = () => (
  <Panel>
    <WorkflowPanel files={[]} onOpen={noop} />
  </Panel>
)

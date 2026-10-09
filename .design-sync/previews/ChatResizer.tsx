/* ChatResizer — the 6px drag handle between the conversation column and the Workspace. It is
 * transparent at rest (a 1px accent line shows only on hover / drag, and it is hidden below the
 * 820px drawer breakpoint), so its static face is the seam itself. The cell shows it where it
 * lives: between the two columns of the studio row, which the handle measures on drag
 * (`parentElement` width) to clamp the chat between CHAT_MIN_PX and the dashboard's minimum.
 * The flanking columns are plain token surfaces standing in for Thread and StudioDashboard. */
import { ChatResizer } from 'agent-app'

const col = (label: string, hint: string, flex: string) => (
  <div
    style={{
      flex,
      minWidth: 0,
      padding: 'var(--sp-card)',
      background: 'var(--bg2)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--r-card)',
      display: 'flex',
      flexDirection: 'column',
      gap: 6,
    }}
  >
    <span style={{ font: 'var(--fw-semi) var(--fs-body) var(--sans)', color: 'var(--text)' }}>{label}</span>
    <span style={{ font: 'var(--fw-normal) var(--fs-meta) var(--sans)', color: 'var(--dim)' }}>{hint}</span>
  </div>
)

export const BetweenTheColumns = () => (
  <div style={{ display: 'flex', height: 220, gap: 0, width: 640 }}>
    <div style={{ display: 'flex', flex: '0 0 300px', order: 0, paddingRight: 6 }}>
      {col('Conversation', 'drag the seam to the right to widen it', '1')}
    </div>
    <ChatResizer side="left" />
    <div style={{ display: 'flex', flex: 1, order: 2, paddingLeft: 6 }}>
      {col('Workspace', 're-columns into whatever width is left', '1')}
    </div>
  </div>
)

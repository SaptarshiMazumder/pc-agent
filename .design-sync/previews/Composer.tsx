/* Composer — the message input. The states that change its chrome: idle, mid-run (abort),
 * and disconnected. */
import { Composer } from 'agent-app'

const noop = () => {}

export const Ready = () => (
  <Composer
    running={false}
    pending={[]}
    onSend={noop}
    onAbort={noop}
    onFiles={noop}
    onRemoveFile={noop}
    connected={true}
    model="claude-sonnet-5"
    credits={12400}
    onCredits={noop}
    maxFiles={8}
    placeholder="Describe the workflow you want…"
    meter={<span className="meter">18% context</span>}
  />
)

export const Running = () => (
  <Composer
    running={true}
    pending={[]}
    onSend={noop}
    onAbort={noop}
    onFiles={noop}
    onRemoveFile={noop}
    connected={true}
    model="claude-sonnet-5"
    credits={12400}
    onCredits={noop}
    maxFiles={8}
    meter={<span className="meter">42% context</span>}
  />
)

export const Disconnected = () => (
  <Composer
    running={false}
    pending={[]}
    onSend={noop}
    onAbort={noop}
    onFiles={noop}
    onRemoveFile={noop}
    connected={false}
    model=""
    credits={null}
    onCredits={noop}
    maxFiles={8}
  />
)

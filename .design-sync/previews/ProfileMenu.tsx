/* ProfileMenu — the account button at the rail's foot. Its content lives in a click-opened
 * portalled menu, so the static face IS the button: the real variant axis is signed-out (○)
 * vs signed-in (avatar letter, accent ring). */
import { ProfileMenu } from 'agent-app'

const noop = () => {}

export const SignedOut = () => (
  <div style={{ width: 280 }}>
    <ProfileMenu auth={null} busy={false} error="" signIn={noop} signOut={async () => {}} onCredits={noop} />
  </div>
)

export const SignedIn = () => (
  <div style={{ width: 280 }}>
    <ProfileMenu
      auth={{ signedIn: true, available: true, email: 'sam@studio.dev' }}
      busy={false}
      error=""
      signIn={noop}
      signOut={async () => {}}
      onCredits={noop}
    />
  </div>
)

export * from './protocol'
export * from './client'
// SIGN-IN AND BILLING, both owned by the CLIENT.
//
//   session.ts  what this client stores: its token, and which keys it wants to pay with
//   auth.ts     ordinary HTTP sign-in against the accounts service, then reconnect
//   gate.ts     the one-line drop-in form over auth.ts
//
// The daemon stores neither fact. It says where to sign in, and reads both off each connection —
// which is what lets one daemon serve many people, and one machine run two windows on two
// different accounts and two different billing modes at once.
export * from './session'
export * from './auth'
export * from './gate'
// The sign-in machinery itself, so an app that wants a credential can ask for one rather than
// reach into storage: `identity().accessToken()` renews first when what is held is spent.
export * from './identity'
export * from './platform-status'
// The token manager itself. Exported because it IS the sign-in surface now: an app that wants a
// credential asks it for one, and a test can drive it without a browser. Re-exported from here
// rather than imported from '@agentd/auth' by callers — an agent has only this one bundle.
export { TokenManager, localSessionStore, memorySessionStore } from '@agentd/auth'
export type { AuthConfig, SecretStore, SessionStore, TokenPair } from '@agentd/auth'

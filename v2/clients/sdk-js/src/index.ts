export * from './protocol'
export * from './client'
// SIGN-IN AND BILLING — the RUNTIME owns the credential now, windows just ask.
//
//   session.ts  what this client stores: its run-mode choice (and a hosted page's borrowed token)
//   auth.ts     sign-in/out as requests to the runtime's local /auth/* endpoints
//   credits.ts  the balance and the shop, wired to identity() + the daemon's accounts url
//
// One machine, one account: the runtime keeps the single refresh token and renews it
// single-flight (platform_session.py); every window reads the same fact over local HTTP.
export * from './session'
export * from './auth'
export * from './credits'
// Organizations and seats. An enterprise buys seats once and its people meet them in the
// assistant AND in every agent, so this is the assistant's own client rather than a second idea
// of what a seat is. Every answer is scoped server-side by membership.
export * from './orgs'
// The sign-in machinery itself, so an app that wants a credential can ask for one rather than
// reach into storage: `identity().accessToken()` renews first when what is held is spent.
export * from './identity'
export * from './platform-status'

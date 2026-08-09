export * from './protocol'
export * from './client'
// SIGN-IN, in two halves that answer two different questions.
//
//   auth.ts      WHO YOU ARE. Three daemon methods; the accounts address and the session token
//                never reach page JavaScript. Works on any install with an accounts service,
//                whoever is paying for the model calls.
//   platform.ts  WHO PAYS. The desktop shell's Cloud switch: hand the daemon a token and it runs
//                model calls on platform keys. Unrelated to being logged in, and keeping the two
//                apart is why an agent can now have a login at all.
//
// gate.ts is the one-line drop-in form over auth.ts.
export * from './auth'
export * from './platform'
export * from './gate'

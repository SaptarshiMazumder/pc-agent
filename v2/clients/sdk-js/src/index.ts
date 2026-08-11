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

export * from './protocol'
export * from './client'
// Hosted sign-in. platform.ts is the mechanism (no DOM, reusable by an app with its own UI);
// gate.ts is the one-line drop-in form. Both are no-ops on a BYOK build, which is what lets the
// scaffold template call the gate unconditionally.
export * from './platform'
export * from './gate'

# @agentd/auth

Sign-in and access-token renewal. One implementation, two consumers:

- `clients/ui` — the agentd client (desktop, web, marketplace)
- `clients/sdk-js` — the agent SDK, vendored into every agent window

**It is a leaf.** It imports nothing from either consumer and must stay that way — the moment it
reaches back into one of them, the other cannot use it and we are back to two implementations.
Everything host-specific arrives through `AuthConfig`: where the accounts service is, where secrets
are kept, what to do when the credential changes.

## Why this package exists

There were three sign-in paths. The agentd client renewed correctly; the SDK re-implemented the
same job and got three things wrong (it refused to renew a token that had already expired, had no
single-flight guard so concurrent windows tripped the server's refresh-reuse detector, and posted
to a different endpoint); the desktop push-down path was a third. A user signed into an agent
window was quietly signed out ten minutes later with no form to sign back in with.

Ship source, not a build: both consumers bundle it themselves (Vite, tsup), so agents still receive
exactly one `agentd-client.js` with this inlined.

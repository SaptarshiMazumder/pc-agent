"""Front-ends that ship WITH the wheel. Each submodule is an INDEPENDENT client that
connects to the gateway over WebSocket (rendezvous via ~/.agentd/gateway.json, or an
explicit --url). Currently: terminal/ (the REPL) and watch (event-log tail).

The desktop shell lives OUTSIDE the package (v2/clients/desktop, an Electron app) —
it is a build product, not python code, but speaks the exact same protocol."""

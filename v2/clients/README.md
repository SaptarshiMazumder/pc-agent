# clients/

Front-ends for the agent. Each is an INDEPENDENT program that connects to the
agentd gateway over WebSocket (rendezvous via ~/.agentd/gateway.json, which also
carries the auth token) and speaks the chat.send / chat.event JSON protocol. They
share NO server code — a client could be written in any language.

- terminal/  — shim; the Python terminal REPL lives IN the package now
               (agent_runtime/clients/terminal, ships with the wheel).
               Run: `agentd chat`  (or `python -m clients.terminal` from a checkout)
- desktop/   — the Electron shell (chat + agents + STORE + daemon supervisor).
               Run: `cd clients/desktop && npm run dev`. See its README.
- watch.py   — shim; event-log tail moved to agent_runtime/clients/watch.

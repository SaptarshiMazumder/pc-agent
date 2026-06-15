# clients/

Front-ends for the agent. Each is an INDEPENDENT program that connects to the
agentd gateway over WebSocket (ws://127.0.0.1:8787) and speaks the chat.send /
chat.event JSON protocol. They share NO code with the server (agentd/) - a client
could be written in any language.

- terminal/  - the Python terminal REPL.   Run:  python -m clients.terminal
- desktop/   - (future) Tauri/Electron shell + agentd sidecar.
- web/       - (future) browser web UI.

To add a front-end: make a folder here, connect to the gateway, and render the
streamed events however your medium wants. Display/decoration is each client's own
concern - the server emits neutral semantic events.

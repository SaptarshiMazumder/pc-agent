"""Back-compat shims. The python clients moved INTO the package (agent_runtime/clients/) so
they ship with the wheel; these thin modules keep the historical dev commands
(`python -m clients.terminal`, `python -m clients.watch`) working from a checkout.
New code should import agent_runtime.clients.*. The desktop shell (Electron) lives in
clients/ui + clients/desktop + clients/web — build products, not python."""

"""Back-compat shims. The python clients moved INTO the package (agentd/clients/) so
they ship with the wheel; these thin modules keep the historical dev commands
(`python -m clients.terminal`, `python -m clients.watch`) working from a checkout.
New code should import agentd.clients.*. The desktop shell (Electron) lives in
clients/desktop — a build product, not python."""

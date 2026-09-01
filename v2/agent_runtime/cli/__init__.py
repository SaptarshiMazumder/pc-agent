"""The `agentd` / `jarvis` console command (M1).

One thin argparse layer over things that already exist: the gateway (serve), the
terminal REPL (chat), the daemon lifecycle (status/stop), plugin manifests and the
agent registry (list), the marketplace service (install/...). Each subcommand is a
module in commands/ exposing ``NAME``/``HELP``/``register(subparsers)`` — adding a
command = adding a module + one line in commands/__init__.py, nothing else.

Bare ``agentd`` (no subcommand) is the product entry: first-run onboarding when
needed, ensure the daemon is up, attach the chat REPL — the `openclaw` feel.

(Deliberately no eager re-exports: the console script targets agent_runtime.cli.main:main
directly, and `python -m agent_runtime.cli.main` stays runpy-clean.)
"""

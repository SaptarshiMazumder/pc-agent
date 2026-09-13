"""Built-in 'ask' bundle — the ``ask_user`` tool (see ask_user_tool). Always registered: an agent
that spends money or builds to a brief needs one sanctioned way to stop and ask, and it must be
the same one on every daemon so the gate that reads it is the same too.
"""

from __future__ import annotations


def register(api, ctx):
    # Imported by bare name: the loader puts this plugin's folder on sys.path, so siblings are
    # top-level modules here rather than a package.
    from ask_user_tool import AskUserTool

    api.register_tool(AskUserTool())

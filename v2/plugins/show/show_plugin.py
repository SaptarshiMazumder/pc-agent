"""Built-in 'show' bundle — universal deliverable helpers: show_files (present a file)
and zip_files (bundle files into a downloadable .zip).

Always registered (no gating): every agent should be able to show / bundle a file it
produced. Producing tools declare their own outputs; these cover everything else.
"""

from __future__ import annotations


def register(api, ctx):
    from show_tool import ShowFilesTool
    from zip_tool import ZipFilesTool

    api.register_tool(ShowFilesTool(ctx.config))
    api.register_tool(ZipFilesTool(ctx.config))

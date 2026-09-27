"""One shell command that runs OUR fixed modules on a Vast machine, through its portal provisioner.

The modules' source travels base64-encoded and is loaded as modules on the machine; data travels
as base64 JSON and is never interpolated as code. Used by the model downloader and the storage
setup, which is why it is its own class.
"""

from __future__ import annotations

import base64
import json
import shlex
import uuid
from pathlib import Path


class GpuCommandBundle:
    @staticmethod
    def command(modules: tuple[str, ...], entry: str, data: dict) -> str:
        """`entry` is fixed Python that reads `data` (already decoded) and `root` (ComfyUI's
        folder on the machine) — never anything an agent wrote."""
        sources = {name: Path(__file__).with_name(name + ".py").read_text(encoding="utf-8") for name in modules}
        bundle = base64.b64encode(json.dumps(sources).encode()).decode()
        payload = base64.b64encode(json.dumps(data).encode()).decode()
        code = (
            "import base64,json,os,sys,types;from pathlib import Path;"
            f"sources=json.loads(base64.b64decode('{bundle}'));"
            "\nfor name,source in sources.items():\n"
            " module=types.ModuleType(name);sys.modules[name]=module;exec(compile(source,name,'exec'),module.__dict__)\n"
            "root=Path(os.environ.get('WORKSPACE','/workspace'))/'ComfyUI'\n"
            f"data=json.loads(base64.b64decode('{payload}'))\n"
            f"{entry}\n"
            # The provisioner caches completed commands; every invocation must run.
            f"# invocation {uuid.uuid4().hex}\n"
        )
        return "python3 -c " + shlex.quote(code)

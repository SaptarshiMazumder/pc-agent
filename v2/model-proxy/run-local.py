"""Run the Platform Model Proxy (LiteLLM proxy) LOCALLY.

Loads provider keys from v2/.env into the proxy's environment (so the proxy holds the keys,
never agentd), sets a dev master key, and starts the proxy on :4000 with litellm.config.yaml.

    python model-proxy/run-local.py        # from v2/, or anywhere — paths are resolved

Per-user auth (custom_auth.py, loaded from this directory because it sits next to the yaml):
set ACCOUNTS_URL (e.g. http://127.0.0.1:4100) to accept accounts session tokens as bearer
credentials, and ACCOUNTS_INTERNAL_KEY to enable proxy-side usage posting to the ledger —
both optional locally; without them only the master key authenticates.

Then point agentd at it:
    AGENTD_MODEL_PROXY_URL=http://127.0.0.1:4000
    AGENTD_MODEL_PROXY_KEY=sk-agentd-local   # or a user's sess_ token (custom auth)
"""

import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

_here = Path(__file__).resolve()
_v2 = _here.parent.parent  # …/v2
_repo = _v2.parent  # …/pc-agent

load_dotenv(_v2 / ".env")  # GEMINI_API_KEY, DEEPSEEK_API_KEY, … into the proxy env
os.environ.setdefault("LITELLM_MASTER_KEY", "sk-agentd-local")
# LiteLLM prints a Unicode banner; force UTF-8 so it doesn't crash on Windows' cp1252 console
os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

_venv_bin = _repo / ".venv" / ("Scripts" if os.name == "nt" else "bin")
_litellm = _venv_bin / ("litellm.exe" if os.name == "nt" else "litellm")
if not _litellm.exists():
    discovered = shutil.which("litellm")
    if not discovered:
        raise SystemExit(
            "LiteLLM is not installed. Install model-proxy/requirements.txt in the active "
            "environment, then run this command again."
        )
    _litellm = Path(discovered)
_argv = [str(_litellm), "--config", str(_here.parent / "litellm.config.yaml"), "--port", "4000"]
os.execv(str(_litellm), _argv)  # replace this process with the proxy, inheriting the loaded env

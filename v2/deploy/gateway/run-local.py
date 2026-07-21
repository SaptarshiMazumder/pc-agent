"""Run the Platform Model Gateway (LiteLLM proxy) LOCALLY.

Loads provider keys from v2/.env into the proxy's environment (so the proxy holds the keys,
never agentd), sets a dev master key, and starts the proxy on :4000 with litellm.config.yaml.

    python deploy/gateway/run-local.py        # from v2/, or anywhere — paths are resolved

Then point agentd at it:
    AGENTD_MODEL_GATEWAY_URL=http://127.0.0.1:4000
    AGENTD_MODEL_GATEWAY_KEY=sk-agentd-local
"""

import os
from pathlib import Path

from dotenv import load_dotenv

_here = Path(__file__).resolve()
_v2 = _here.parents[2]  # …/v2
_repo = _here.parents[3]  # …/pc-agent

load_dotenv(_v2 / ".env")  # GEMINI_API_KEY, DEEPSEEK_API_KEY, … into the proxy env
os.environ.setdefault("LITELLM_MASTER_KEY", "sk-agentd-local")
# LiteLLM prints a Unicode banner; force UTF-8 so it doesn't crash on Windows' cp1252 console
os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

_litellm = _repo / ".venv" / "Scripts" / ("litellm.exe" if os.name == "nt" else "litellm")
_argv = [str(_litellm), "--config", str(_here.parent / "litellm.config.yaml"), "--port", "4000"]
os.execv(str(_litellm), _argv)  # replace this process with the proxy, inheriting the loaded env

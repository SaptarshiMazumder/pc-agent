"""Load one service's private JSON file, then replace this process. Never log values."""

import json
import os
import re
import sys
from pathlib import Path


def main():
    path = os.environ.get("SERVICE_SECRETS_FILE")
    if path:
        data = json.loads(Path(path).read_text())
        if not isinstance(data, dict) or any(
            not re.fullmatch(r"[A-Z][A-Z0-9_]*", k) or not isinstance(v, str)
            for k, v in data.items()
        ):
            raise ValueError("invalid service secret document")
        os.environ.update(data)
    if len(sys.argv) < 2:
        raise ValueError("service command required")
    os.execvpe(sys.argv[1], sys.argv[1:], os.environ)


if __name__ == "__main__":
    main()

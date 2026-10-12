"""Chromium egress in a disposable task must pass through the public-address proxy."""
import os


def options() -> dict:
    if os.environ.get("AGENTD_ISOLATED_TASK") != "1":
        return {}
    return {"proxy": {"server": os.environ["HTTP_PROXY"]},
            "args": ["--disable-quic", "--proxy-bypass-list=<-loopback>"],
            # Fargate does not permit Chromium's nested user namespace. The
            # disposable task is already the unprivileged VM security boundary.
            "chromium_sandbox": False}

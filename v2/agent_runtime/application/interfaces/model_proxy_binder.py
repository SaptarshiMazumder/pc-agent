"""ModelProxyBinder port — turn platform-key billing on and off.

Binding means two things happen together: the credential is persisted, and the live model proxy is
reconfigured so the very next call routes through it. Splitting them is how you get a daemon that
says it is in Cloud mode until it restarts, or one that is billing an account nothing in the UI
mentions.

One adapter: ``infrastructure/env_file_model_proxy_binder.py``.

Note what this port does NOT touch: the identity token. Being signed in and being billed are
different states, and an install can be in either without the other.
"""

from __future__ import annotations

from typing import Protocol


class ModelProxyBinder(Protocol):
    @property
    def available(self) -> bool:
        """Is a model-proxy URL configured for this build? False => there is no cloud to enter."""
        ...

    @property
    def bound(self) -> bool:
        """Are platform keys currently paying?"""
        ...

    def bind(self, token: str) -> None:
        """Route model calls through the proxy on ``token``, live and across restarts."""
        ...

    def unbind(self) -> None:
        """Back to the user's own provider keys, live and across restarts."""
        ...

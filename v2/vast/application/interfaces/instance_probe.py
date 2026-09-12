"""The one question the service asks a rented machine: are you serving yet?

WHY THIS EXISTS. A machine is `running` at the marketplace for minutes before ComfyUI inside it
answers — the image is still pulling, models are still loading — and declaring `ready` the
moment the port was mapped handed the agent an address that refused it. One model dialled it
three times, decided the box was stuck, and released it mid-boot, throwing away the minutes
already paid for. "Ready" has to mean "answers", and only a call to the machine can say so.

WHY A PORT. So the service stays free of urllib, and a test can say "not yet" without a socket.
"""

from __future__ import annotations

from typing import Protocol


class InstanceProbe(Protocol):
    def answers(self, url: str) -> bool:
        """True when ComfyUI at `url` serves its API. False for everything else — refused,
        timed out, a non-200, a body that is not JSON. NEVER RAISES: the caller is a poll, and
        the next poll is the retry."""
        ...

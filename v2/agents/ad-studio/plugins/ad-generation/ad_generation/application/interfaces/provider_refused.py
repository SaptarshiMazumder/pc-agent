"""A provider turned the input down — moderation, a likeness filter, a rejected file.

Raised by a generator adapter when the provider answers with a refusal rather than a failure:
the same input sent again would be refused again, so a step records it as the shot's outcome,
with the provider's own reason, and does not retry. Any other error is still raised as is.
"""

from __future__ import annotations


class ProviderRefused(Exception):
    def __init__(self, provider: str, model: str, reason: str) -> None:
        super().__init__(f"{provider} refused the input for {model}: {reason}")
        self.provider = provider
        self.model = model
        self.reason = reason

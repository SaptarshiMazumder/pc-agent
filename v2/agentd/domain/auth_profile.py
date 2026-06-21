"""AuthProfile — one credential for a provider, with rotation/cooldown state (S16).

Lets an agent hold several accounts for a provider (e.g. two Google logins, several API
keys) and rotate between them, cooling one down on failure (rate-limit/auth/billing) and
falling back to another. Pure domain — the actual secret lives elsewhere; this is the
selection state. (Wiring into the MCP/model-provider auth is the integration step.)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AuthProfile:
    id: str
    provider: str               # e.g. "google", "openai"
    label: str                  # human label, e.g. "work account"
    enabled: bool = True
    cooldown_until: float = 0.0  # not selectable until this epoch (set on failure)
    failures: int = 0
    last_used: float = 0.0       # for least-recently-used rotation

"""Credential — one site's saved login for an agent.

``username``/``password`` are SECRETS (stored encrypted, decrypted only in-process to fill a
login form, NEVER returned to the model). The rest is non-secret login config: the login URL
plus optional CSS selectors for the username/password/submit/OTP/success elements — blank
selectors fall back to generic heuristics in the login tool. Pure domain data, no IO.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Credential:
    site: str                       # the saved-login name, e.g. "hotpepper"
    login_url: str                  # the page to open to log in
    username: str                   # email / username  (secret-ish)
    password: str                   # SECRET — never shown to the model
    user_selector: str = ""         # CSS for the username/email field (blank -> heuristic)
    pass_selector: str = ""         # CSS for the password field (blank -> heuristic)
    submit_selector: str = ""       # CSS for submit (blank -> press Enter)
    otp_selector: str = ""          # CSS for the 2FA code field (blank -> heuristic; "" + none = no 2FA)
    success_selector: str = ""      # CSS that proves login succeeded (blank -> "password field gone")


# the fields persisted to the vault (everything except the redundant `site` key)
CREDENTIAL_FIELDS = (
    "login_url", "username", "password", "user_selector", "pass_selector",
    "submit_selector", "otp_selector", "success_selector",
)

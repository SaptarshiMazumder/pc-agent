"""add_login — securely capture a site's login for an agent into the encrypted vault (one-time).

  python -m agent_runtime.main.add_login --genkey            # print a vault master key for AGENTD_VAULT_KEY
  python -m agent_runtime.main.add_login <agent> <site>      # prompt for url / username / password (no echo)

The password is read with getpass (no echo, never in shell history) and written straight to the
Fernet-encrypted vault — it never appears on screen or in any log. Requires AGENTD_VAULT_KEY set.
This is the local/dev capture path; the production path is the one-time secure /connect web form.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent_runtime.config import load_config
from agent_runtime.domain.credential import Credential


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] in ("--genkey", "-g"):
        from cryptography.fernet import Fernet

        print(Fernet.generate_key().decode())
        print(
            "# ^ set this as AGENTD_VAULT_KEY (env / .env) — keep it secret, never commit it",
            file=sys.stderr,
        )
        return
    if len(args) < 2:
        print(
            "usage: python -m agent_runtime.main.add_login <agent> <site>   (or --genkey)",
            file=sys.stderr,
        )
        sys.exit(2)

    agent, site = args[0].strip(), args[1].strip()
    cfg = load_config()  # loads v2/.env -> AGENTD_VAULT_KEY into the env
    from agent_runtime.infrastructure.credentials import build_credential_store

    store = build_credential_store(cfg)
    if store is None:
        print(
            "AGENTD_VAULT_KEY is not set. Run `python -m agent_runtime.main.add_login --genkey`, "
            "set it in your .env, and retry.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Connecting login '{site}' for agent '{agent}' (password is hidden, stored encrypted):")
    login_url = input("  login page URL: ").strip()
    username = input("  username / email: ").strip()
    password = getpass.getpass("  password (hidden): ")
    otp_sel = input("  OTP field CSS selector [optional, blank if no 2FA]: ").strip()
    user_sel = input("  username field CSS selector [optional]: ").strip()
    pass_sel = input("  password field CSS selector [optional]: ").strip()
    if not (login_url and username and password):
        print("login_url, username and password are required.", file=sys.stderr)
        sys.exit(1)

    store.put(
        agent,
        Credential(
            site=site,
            login_url=login_url,
            username=username,
            password=password,
            user_selector=user_sel,
            pass_selector=pass_sel,
            otp_selector=otp_sel,
        ),
    )
    print(f"Saved ✓  agent '{agent}' can now use simple_login(site='{site}').")


if __name__ == "__main__":
    main()

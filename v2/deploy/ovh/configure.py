"""Prepare private host files without printing credentials or touching Neon.

Run once as root on the Linux host. Feed existing credentials through a private
JSON document outside git. Refuse to overwrite an existing deployment's keys.
"""

import argparse
import json
import os
import secrets
import shlex
from pathlib import Path

from cryptography.fernet import Fernet

ROTATABLE = {
    "GEMINI_API_KEY": ["model-proxy", "daemon"],
    "DEEPSEEK_API_KEY": ["model-proxy"],
    "MOONSHOT_API_KEY": ["model-proxy"],
    "OPENAI_API_KEY": ["model-proxy"],
    "ANTHROPIC_API_KEY": ["model-proxy"],
    "AGENTD_OIDC_GOOGLE_CLIENT_ID": ["accounts"],
    "AGENTD_OIDC_GOOGLE_CLIENT_SECRET": ["accounts"],
    "GOOGLE_OAUTH_CLIENT_ID": ["daemon"],
    "GOOGLE_OAUTH_CLIENT_SECRET": ["daemon"],
    "DODO_API_KEY": ["accounts"],
    "DODO_WEBHOOK_SECRET": ["accounts"],
    "DODO_PRODUCT_ID": ["accounts"],
    "DODO_API_BASE_URL": ["accounts"],
    "COMFY_API_KEY": ["daemon"],
    "HF_TOKEN": ["daemon"],
    "CIVITAI_TOKEN": ["daemon"],
}


def write(path, content, uid=1000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    os.chmod(path, 0o600)
    if os.name != "nt":
        os.chown(path.parent, uid, uid)
        os.chmod(path.parent, 0o700)
        os.chown(path, uid, uid)


def configure(values, root):
    root = Path(root)
    if (root / "secrets").exists():
        raise ValueError("deployment secrets already exist; refusing to overwrite or rotate keys")
    required = (
        "DATABASE_URL",
        "AGENTD_IDENTITY_KEK",
        "AGENTD_STATE_KEK",
        "CLOUDFLARE_API_TOKEN",
        "R2_PUBLISH_ACCESS_KEY_ID",
        "R2_PUBLISH_SECRET_ACCESS_KEY",
        "R2_SANDBOX_ACCESS_KEY_ID",
        "R2_SANDBOX_SECRET_ACCESS_KEY",
        "RESTIC_REPOSITORY",
        "R2_BACKUP_ACCESS_KEY_ID",
        "R2_BACKUP_SECRET_ACCESS_KEY",
    )
    if not isinstance(values, dict) or any(not isinstance(v, str) for v in values.values()):
        raise ValueError("operator input must be a JSON object containing strings")
    for key in required:
        if not values.get(key) or values[key] == "REPLACE_ME":
            raise ValueError(
                f"{key} is required; use the existing production value where applicable"
            )
    if not values["DATABASE_URL"].startswith(("postgres://", "postgresql://")):
        raise ValueError("use the existing Neon Postgres URL")
    if any(c in values["CLOUDFLARE_API_TOKEN"] for c in "\n\r\x00"):
        raise ValueError("invalid Cloudflare token")
    # Identity/state derive their wrapping keys from the existing passphrases.
    # Do not re-encode those values; that would strand encrypted production rows.
    for folder in ("accounts", "daemon", "publish", "runtime", "scheduler"):
        directory = root / folder
        directory.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chown(directory, 1000, 1000)
            os.chmod(directory, 0o700)
    internal = {
        name: secrets.token_urlsafe(48)
        for name in (
            "ACCOUNTS_INTERNAL_KEY",
            "LITELLM_MASTER_KEY",
            "AGENTD_TOKEN",
            "BUILDER_INTERNAL_KEY",
            "EXECUTOR_INTERNAL_KEY",
            "RUNTIME_CONTROL_KEY",
            "PLATFORM_CONTROL_KEY",
        )
    }
    documents = {
        service: {}
        for service in (
            "accounts",
            "model-proxy",
            "daemon",
            "publish",
            "executor",
            "builder",
            "controller",
            "scheduler",
        )
    }
    documents["accounts"].update(
        {key: values[key] for key in ("DATABASE_URL", "AGENTD_IDENTITY_KEK")}
    )
    documents["accounts"].update(
        {key: internal[key] for key in ("ACCOUNTS_INTERNAL_KEY", "PLATFORM_CONTROL_KEY")}
    )
    documents["model-proxy"].update(
        {key: internal[key] for key in ("ACCOUNTS_INTERNAL_KEY", "LITELLM_MASTER_KEY")}
    )
    documents["daemon"].update(
        AGENTD_STATE_KEK=values["AGENTD_STATE_KEK"],
        AGENTD_TOKEN=internal["AGENTD_TOKEN"],
        AGENTD_ACCOUNTS_INTERNAL_KEY=internal["ACCOUNTS_INTERNAL_KEY"],
        AGENTD_MODEL_PROXY_KEY=internal["LITELLM_MASTER_KEY"],
        AGENTD_EXECUTOR_INTERNAL_KEY=internal["EXECUTOR_INTERNAL_KEY"],
        AGENTD_BUILDER_INTERNAL_KEY=internal["BUILDER_INTERNAL_KEY"],
    )
    for service, credential in (
        ("publish", "PUBLISH"),
        ("executor", "SANDBOX"),
        ("builder", "SANDBOX"),
    ):
        documents[service].update(
            OBJECT_STORAGE_ACCESS_KEY_ID=values[f"R2_{credential}_ACCESS_KEY_ID"],
            OBJECT_STORAGE_SECRET_ACCESS_KEY=values[f"R2_{credential}_SECRET_ACCESS_KEY"],
        )
    documents["publish"]["PUBLISH_KEY_KEK"] = (
        values.get("PUBLISH_KEY_KEK") or Fernet.generate_key().decode()
    )
    Fernet(documents["publish"]["PUBLISH_KEY_KEK"].encode())
    for service in ("executor", "builder"):
        documents[service].update(
            RUNTIME_CONTROL_KEY=internal["RUNTIME_CONTROL_KEY"],
            **{service.upper() + "_INTERNAL_KEY": internal[service.upper() + "_INTERNAL_KEY"]},
        )
    documents["controller"].update(
        {key: internal[key] for key in ("RUNTIME_CONTROL_KEY", "PLATFORM_CONTROL_KEY")}
    )
    documents["scheduler"]["ACCOUNTS_INTERNAL_KEY"] = internal["ACCOUNTS_INTERNAL_KEY"]
    for key, consumers in ROTATABLE.items():
        for service in consumers:
            documents[service][key] = values.get(key, "")
    for service, document in documents.items():
        write(root / "secrets" / service / "env.json", json.dumps(document, indent=2) + "\n")
    write(root / "secrets" / "consumers.json", json.dumps(ROTATABLE, indent=2) + "\n", uid=0)
    write(
        root / "secrets" / "caddy.env",
        "CLOUDFLARE_API_TOKEN=" + values["CLOUDFLARE_API_TOKEN"] + "\n",
        uid=0,
    )
    backup = dict(
        RESTIC_REPOSITORY=values["RESTIC_REPOSITORY"],
        RESTIC_PASSWORD=values.get("RESTIC_PASSWORD") or secrets.token_urlsafe(48),
        AWS_ACCESS_KEY_ID=values["R2_BACKUP_ACCESS_KEY_ID"],
        AWS_SECRET_ACCESS_KEY=values["R2_BACKUP_SECRET_ACCESS_KEY"],
        AWS_DEFAULT_REGION="auto",
    )
    write(
        root / "secrets" / "backup.env",
        "".join(f"export {k}={shlex.quote(v)}\n" for k, v in backup.items()),
        uid=0,
    )
    return root


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--state-root", default="/opt/agentd/state")
    args = parser.parse_args()
    configure(json.loads(Path(args.input).read_text()), args.state_root)
    print("Private service files prepared. Preserve encryption keys and backup password offline.")

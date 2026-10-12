"""Validate production inputs without emitting any values."""

import json
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit


def main():
    env = {}
    for line in Path(".env").read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    for key in (
        "TLS_EMAIL",
        "ADMIN_IDENTITIES",
        "OBJECT_STORAGE_ENDPOINT",
        "REGISTRY_PUBLIC_BASE",
        "REGISTRY_PUBLISHER_KEY",
        "SCRATCH_BUCKET",
        "REGISTRY_BUCKET",
        "PENDING_BUCKET",
    ):
        if not env.get(key) or "REPLACE_ME" in env[key]:
            raise ValueError(f"{key} must be configured")
    if len({env[k] for k in ("SCRATCH_BUCKET", "REGISTRY_BUCKET", "PENDING_BUCKET")}) != 3:
        raise ValueError("scratch, public registry and private submissions need distinct buckets")
    for key in ("OBJECT_STORAGE_ENDPOINT", "REGISTRY_PUBLIC_BASE"):
        if urlsplit(env[key]).scheme != "https":
            raise ValueError(f"{key} must use HTTPS")
    root = Path(env.get("STATE_ROOT", "/opt/agentd/state"))
    documents = {
        s: json.loads((root / "secrets" / s / "env.json").read_text())
        for s in (
            "accounts",
            "daemon",
            "publish",
            "executor",
            "builder",
            "controller",
            "model-proxy",
            "scheduler",
        )
    }
    database = root / "publish" / "registry.db"
    if not database.is_file():
        raise ValueError("import the existing publisher signing identities before deploying")
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
        row = db.execute("SELECT document FROM creators WHERE id='__root__'").fetchone()
    if not row or json.loads(row[0])["public_key"] != env["REGISTRY_PUBLISHER_KEY"]:
        raise ValueError("publisher root must match the existing client trust anchor")
    for service, fields in {
        "accounts": [
            "DATABASE_URL",
            "AGENTD_IDENTITY_KEK",
            "ACCOUNTS_INTERNAL_KEY",
            "AGENTD_OIDC_GOOGLE_CLIENT_ID",
            "AGENTD_OIDC_GOOGLE_CLIENT_SECRET",
        ],
        "daemon": ["AGENTD_STATE_KEK"],
        "publish": ["PUBLISH_KEY_KEK"],
    }.items():
        for field in fields:
            if not documents[service].get(field) or documents[service][field] == "REPLACE_ME":
                raise ValueError(f"{service}: {field} must contain the existing valid value")
    if env.get("PAYMENT_PROVIDER", "dodo") == "dodo":
        for key in ("DODO_API_KEY", "DODO_WEBHOOK_SECRET", "DODO_PRODUCT_ID", "DODO_API_BASE_URL"):
            if not documents["accounts"].get(key) or documents["accounts"][key] == "REPLACE_ME":
                raise ValueError(f"accounts: {key} must be configured")
    for service in ("executor", "builder", "publish"):
        for key in ("OBJECT_STORAGE_ACCESS_KEY_ID", "OBJECT_STORAGE_SECRET_ACCESS_KEY"):
            if not documents[service].get(key) or documents[service][key] == "REPLACE_ME":
                raise ValueError(f"{service}: {key} must be configured")
    pairs = [
        ("accounts", "ACCOUNTS_INTERNAL_KEY", "model-proxy", "ACCOUNTS_INTERNAL_KEY"),
        ("accounts", "ACCOUNTS_INTERNAL_KEY", "daemon", "AGENTD_ACCOUNTS_INTERNAL_KEY"),
        ("accounts", "ACCOUNTS_INTERNAL_KEY", "scheduler", "ACCOUNTS_INTERNAL_KEY"),
        ("model-proxy", "LITELLM_MASTER_KEY", "daemon", "AGENTD_MODEL_PROXY_KEY"),
        ("accounts", "PLATFORM_CONTROL_KEY", "controller", "PLATFORM_CONTROL_KEY"),
        ("executor", "RUNTIME_CONTROL_KEY", "controller", "RUNTIME_CONTROL_KEY"),
        ("builder", "RUNTIME_CONTROL_KEY", "controller", "RUNTIME_CONTROL_KEY"),
        ("executor", "EXECUTOR_INTERNAL_KEY", "daemon", "AGENTD_EXECUTOR_INTERNAL_KEY"),
        ("builder", "BUILDER_INTERNAL_KEY", "daemon", "AGENTD_BUILDER_INTERNAL_KEY"),
    ]
    for left, left_key, right, right_key in pairs:
        value = documents[left].get(left_key, "")
        if len(value) < 32 or value != documents[right].get(right_key):
            raise ValueError(f"internal credential mismatch between {left} and {right}")
    print("Production configuration validated; credentials were not printed.")


if __name__ == "__main__":
    main()

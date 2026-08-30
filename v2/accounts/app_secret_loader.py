"""AppSecretLoader — the app secret, from Secrets Manager, into this process's environment.

ONE SOURCE OF TRUTH FOR SECRETS, EVERYWHERE THE SERVICE RUNS. On ECS the task definition
already injects secret fields as env vars; a developer's machine had no equivalent, so local
runs read whatever env vars happened to be exported — a second, unmanaged place for keys to
live, drift, and leak. This loader closes that: at startup the service pulls the SAME secret
the deployment uses (`AGENTD_APP_SECRET_ID`) and loads it over the environment. Secrets
Manager WINS over anything pre-set, because a locally exported override that silently beats
the vault is exactly the drift being eliminated.

ONLY THE FIELDS THE CALLER DECLARES ARE LOADED. The app secret is the platform's whole vault —
model-provider keys included — and this service has no business holding keys it never reads.
The declared tuple mirrors the service's `secret_keys` map in infra/modules/variables.tf: the
same statement of need, made where the code runs.

A `REPLACE_ME` FIELD IS NOT LOADED. That sentinel is this repo's "field exists, value was never
set" marker (infra/modules/data.tf seeds it, set-keys.ps1 reports it). Loading it would turn
"RAZORPAY_KEY_ID is not set" — the payment factory's clear refusal — into "Razorpay rejected
your credentials", a worse error pointing at the wrong culprit.

FAILURE IS FATAL, NEVER A FALLBACK. If the secret id is set but the fetch fails — no AWS
credentials, no network, no such secret — the service must not shrug and boot on ambient env
vars: that silently reintroduces the second source of truth. The caller lets the raise kill
startup.
"""

from __future__ import annotations

import json
import logging
import os

log = logging.getLogger("accounts")

#: The repo-wide "defined but never set" sentinel (infra/modules/data.tf, set-keys.ps1).
PLACEHOLDER = "REPLACE_ME"


class AppSecretUnavailable(RuntimeError):
    """The app secret cannot be read as configured. Startup must stop — booting without it
    would run the service on whatever env vars happen to be lying around."""


class AppSecretLoader:
    def __init__(self, secret_id: str, *, fields: tuple[str, ...], region: str = "") -> None:
        if not secret_id:
            raise AppSecretUnavailable("a Secrets Manager secret id is required")
        self._secret_id = secret_id
        self._fields = fields
        self._region = region

    def load_into_environ(self) -> list[str]:
        """Fetch the secret and export the declared fields. Returns the names loaded."""
        secret = self._fetch()
        loaded: list[str] = []
        for field in self._fields:
            value = str(secret.get(field) or "").strip()
            if not value or value == PLACEHOLDER:
                continue
            os.environ[field] = value
            loaded.append(field)
        log.info(
            "app secret %s: loaded %d of %d declared fields (%s)",
            self._secret_id, len(loaded), len(self._fields), ", ".join(loaded) or "none",
        )
        return loaded

    def _fetch(self) -> dict:
        # Deferred import, the documented admin_api._boto pattern: the SDK is in requirements,
        # but importing it at module load would put boto3 on the import path of every test that
        # loads a sibling by file.
        try:
            import boto3  # noqa: PLC0415 - deliberately deferred; see comment above
        except ImportError as e:  # pragma: no cover - requirements ship boto3
            raise AppSecretUnavailable("boto3 is not installed; cannot read the app secret") from e
        try:
            client = boto3.client("secretsmanager", region_name=self._region or None)
            raw = client.get_secret_value(SecretId=self._secret_id)["SecretString"]
        except Exception as e:  # noqa: BLE001 - every botocore failure means the same thing here
            raise AppSecretUnavailable(
                f"cannot read app secret {self._secret_id!r} from Secrets Manager: {e}. "
                f"The service refuses to start on ambient env vars instead."
            ) from e
        try:
            secret = json.loads(raw)
        except ValueError as e:
            raise AppSecretUnavailable(
                f"app secret {self._secret_id!r} is not a JSON object"
            ) from e
        if not isinstance(secret, dict):
            raise AppSecretUnavailable(f"app secret {self._secret_id!r} is not a JSON object")
        return secret

"""License files — the OFFLINE half of commercial entitlement (M7).

A license is a JSON file in ~/.agentd/licenses/*.lic:

    {"type": "agentd-license", "payload": "<base64 of the claims JSON>", "sig": "<base64 ed25519>"}

with claims:

    {"sku": "figure-creator-pro", "skus": [...],   # one or many entitled SKUs
     "exp": "2027-07-04",                           # ISO date; "" => perpetual
     "iss": "agentd", "sub": "customer@email"}      # informational

The signature is over the RAW payload bytes with the PUBLISHER's private key; the
client verifies against the pinned public key from distribution.toml. Everything
verifies offline (a signed file is the proof) — Phase 5 payments only need to
DELIVER such a file after checkout. Invalid/expired/tampered licenses are ignored
with a log line; verification never crashes the app (fail closed = fewer SKUs)."""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from agentd.infrastructure import signing

log = logging.getLogger("agentd")


@dataclass(frozen=True)
class License:
    skus: tuple[str, ...]
    expires: str = ""          # ISO date, "" => perpetual
    issuer: str = ""
    subject: str = ""
    path: str = ""


def issue_license(private_key_b64: str, skus: list[str], expires: str = "",
                  issuer: str = "agentd", subject: str = "") -> str:
    """Publisher-side: -> the license file CONTENT (JSON text). Written by
    `agentd license issue`; later, by the registry backend after checkout."""
    claims = {"skus": sorted(set(skus)), "exp": expires, "iss": issuer, "sub": subject}
    payload = json.dumps(claims, sort_keys=True).encode("utf-8")
    return json.dumps({
        "type": "agentd-license",
        "payload": base64.b64encode(payload).decode(),
        "sig": signing.sign(private_key_b64, payload),
    }, indent=2) + "\n"


def parse_license(text: str, publisher_key_b64: str, path: str = "") -> License | None:
    """Verify + parse one license file. None (logged) on ANY problem — a bad license
    simply doesn't entitle, it never breaks startup."""
    try:
        envelope = json.loads(text)
        if envelope.get("type") != "agentd-license":
            raise ValueError("not an agentd-license")
        payload = base64.b64decode(envelope["payload"])
        if not signing.verify(publisher_key_b64, payload, envelope.get("sig", "")):
            raise ValueError("signature invalid")
        claims = json.loads(payload)
        skus = tuple(str(s) for s in (claims.get("skus") or
                                      ([claims["sku"]] if claims.get("sku") else [])))
        if not skus:
            raise ValueError("no skus")
        expires = str(claims.get("exp") or "")
        if expires and date.fromisoformat(expires) < date.today():
            raise ValueError(f"expired {expires}")
        return License(skus=skus, expires=expires, issuer=str(claims.get("iss") or ""),
                       subject=str(claims.get("sub") or ""), path=path)
    except Exception as e:  # noqa: BLE001 — fail closed, loudly, never fatally
        log.warning("licensing: ignoring %s: %s", path or "license", e)
        return None


def load_licenses(licenses_dir: Path, publisher_key_b64: str) -> list[License]:
    """Every VALID license in the dir. No pinned publisher key => none can verify."""
    if not publisher_key_b64 or not licenses_dir.is_dir():
        return []
    licenses = []
    for path in sorted(licenses_dir.glob("*.lic")):
        try:
            parsed = parse_license(path.read_text(encoding="utf-8"), publisher_key_b64, str(path))
        except OSError as e:
            log.warning("licensing: cannot read %s: %s", path, e)
            continue
        if parsed is not None:
            licenses.append(parsed)
            log.info("licensing: %s -> skus %s%s", path.name, ", ".join(parsed.skus),
                     f" (until {parsed.expires})" if parsed.expires else "")
    return licenses


def entitled_skus(licenses: list[License]) -> set[str]:
    return {sku for lic in licenses for sku in lic.skus}

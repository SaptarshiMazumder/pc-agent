"""M7: license files (issue -> verify -> entitle) + the LicenseEntitlement policy."""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.infrastructure import signing
from agentd.infrastructure.licensing import entitled_skus, issue_license, load_licenses
from agentd.infrastructure.licensing.license_store import parse_license
from agentd.infrastructure.plugins.entitlement import LicenseEntitlement
from agentd.infrastructure.plugins.manifest import PluginManifest


def _keys():
    return signing.generate_keypair()


def test_issue_verify_roundtrip(tmp_path):
    private_b64, public_b64 = _keys()
    (tmp_path / "pro.lic").write_text(
        issue_license(private_b64, ["figure-creator-pro"], subject="user@example.com"),
        encoding="utf-8")
    licenses = load_licenses(tmp_path, public_b64)
    assert len(licenses) == 1
    assert entitled_skus(licenses) == {"figure-creator-pro"}
    assert licenses[0].subject == "user@example.com"


def test_tampered_license_rejected(tmp_path):
    private_b64, public_b64 = _keys()
    content = issue_license(private_b64, ["pro"])
    tampered = content.replace('"skus": ["pro"]', '"skus": ["pro", "everything"]')
    assert parse_license(content, public_b64) is not None
    # tampering the payload without re-signing must fail (payload is b64 inside, so
    # simulate by signing with the WRONG key instead)
    wrong_private, _ = _keys()
    forged = issue_license(wrong_private, ["everything"])
    assert parse_license(forged, public_b64) is None
    assert parse_license(tampered, public_b64) is not None  # envelope text unused for sig
    # (the b64 payload IS the signed message — editing the readable text is a no-op)


def test_expired_license_rejected():
    private_b64, public_b64 = _keys()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert parse_license(issue_license(private_b64, ["pro"], expires=yesterday), public_b64) is None
    assert parse_license(issue_license(private_b64, ["pro"], expires=tomorrow), public_b64) is not None


def test_no_publisher_key_verifies_nothing(tmp_path):
    private_b64, _ = _keys()
    (tmp_path / "pro.lic").write_text(issue_license(private_b64, ["pro"]), encoding="utf-8")
    assert load_licenses(tmp_path, "") == []


def test_license_entitlement_policy():
    free = PluginManifest(id="web", name="Web", kind="native", entry="x:y")
    paid = PluginManifest(id="figures", name="Figures", kind="native", entry="x:y",
                          entitlement="figure-creator-pro")
    policy = LicenseEntitlement(set())
    assert policy.is_entitled(free), "unmarked plugins are always free"
    assert not policy.is_entitled(paid)
    licensed = LicenseEntitlement({"figure-creator-pro"})
    assert licensed.is_entitled(paid)

"""The Lambda's HTTP layer: routing, the bearer token, and multipart parsing.

Multipart parsing is the riskiest code in the service, because it is the one place a mistake is
INVISIBLE and total: a body decoded as text instead of base64 corrupts the zip, and the only symptom
an author ever sees is "not a valid .agentpkg" for a package that is perfectly fine.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "publish"))

import handler as publish_handler  # noqa: E402

BOUNDARY = "----agentdtest"


def multipart(files: dict[str, tuple[str, bytes]], fields: dict[str, str] | None = None) -> bytes:
    parts = []
    for name, value in (fields or {}).items():
        parts.append(
            f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    for name, (filename, data) in files.items():
        parts.append(
            f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{name}"; '
            f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
            + data
            + b"\r\n"
        )
    parts.append(f"--{BOUNDARY}--\r\n".encode())
    return b"".join(parts)


def event(body: bytes, *, token="tok", path="/registry/publish", method="POST", b64=True) -> dict:
    return {
        "path": path,
        "httpMethod": method,
        "headers": {
            "Content-Type": f"multipart/form-data; boundary={BOUNDARY}",
            "Authorization": f"Bearer {token}",
        },
        "isBase64Encoded": b64,
        "body": base64.b64encode(body).decode() if b64 else body.decode("latin-1"),
    }


class Recorder:
    """Stands in for the intake service, so these tests are only about HTTP."""

    def __init__(self, status=200, message="ok"):
        from agent_runtime.application.interfaces.publish_intake import IntakeResult

        self.seen = None
        self._result = IntakeResult(status, message, bundle_id="weather", version="1.0.0")

    def submit(self, submission):
        self.seen = submission
        return self._result


@pytest.fixture
def recorder(monkeypatch):
    r = Recorder()
    monkeypatch.setattr(publish_handler, "service", lambda: r)
    return r


# ────────────────────────────── routing ──────────────────────────────


def test_an_unknown_path_is_404(recorder):
    response = publish_handler.handler(event(b"", path="/nope"))
    assert response["statusCode"] == 404
    assert recorder.seen is None


def test_a_get_is_405_with_a_usable_hint(recorder):
    response = publish_handler.handler(event(b"", method="GET"))
    assert response["statusCode"] == 405
    assert "multipart" in json.loads(response["body"])["message"]


def test_the_route_matches_when_the_alb_prefixes_a_stage(recorder):
    """API Gateway stages and ALB rules both prepend paths; matching on the suffix survives both."""
    body = multipart({"package": ("weather-1.0.0.agentpkg", b"PK\x03\x04zip")})
    response = publish_handler.handler(event(body, path="/prod/registry/publish"))
    assert response["statusCode"] == 200


# ────────────────────────────── the body ──────────────────────────────


def test_a_binary_package_survives_base64_transport_byte_for_byte():
    """The bug this pins: decoding the body as text corrupts the zip, and the author is told their
    package is invalid when it is not."""
    package = bytes(range(256)) * 4  # every byte value, including ones no text codec round-trips
    fields, files, filenames = publish_handler._parse_multipart(
        event(multipart({"package": ("x.agentpkg", package)}))
    )
    assert files["package"] == package
    assert filenames["package"] == "x.agentpkg"


def test_text_fields_come_through_alongside_the_file():
    fields, files, filenames = publish_handler._parse_multipart(
        event(multipart({"package": ("x.agentpkg", b"zip")}, {"bundle_id": "weather"}))
    )
    assert fields["bundle_id"] == "weather"
    assert files["package"] == b"zip"


def test_each_file_part_keeps_its_own_name(recorder):
    """The bug this pins, and it shipped: every file part wrote its name into ONE shared slot, so
    with `package` followed by `installer` the package inherited `<id>-<ver>-setup.exe`. That name
    became the payload's bundles/ entry, and the engine — which globs `*.agentpkg` — could not see
    it. The installed app opened with no agent in it and nothing said why."""
    body = multipart(
        {
            "package": ("weather-1.0.0.agentpkg", b"PK\x03\x04zip"),
            "installer": ("weather-1.0.0-setup.exe", b"MZexe"),
        }
    )
    fields, files, filenames = publish_handler._parse_multipart(event(body))
    assert filenames["package"] == "weather-1.0.0.agentpkg"
    assert filenames["installer"] == "weather-1.0.0-setup.exe"

    publish_handler.handler(event(body))
    assert recorder.seen.filename == "weather-1.0.0.agentpkg"
    assert recorder.seen.package == b"PK\x03\x04zip"


def test_a_body_with_no_file_part_is_refused():
    with pytest.raises(ValueError, match="no file part"):
        publish_handler._parse_multipart(event(multipart({}, {"bundle_id": "weather"})))


def test_a_non_multipart_body_is_refused_with_a_400(recorder):
    bad = event(b"{}")
    bad["headers"]["Content-Type"] = "application/json"
    response = publish_handler.handler(bad)
    assert response["statusCode"] == 400
    assert "multipart" in json.loads(response["body"])["message"]


def test_the_submission_carries_the_token_the_id_and_the_filename(recorder):
    body = multipart({"package": ("weather-1.0.0.agentpkg", b"zip")}, {"bundle_id": "weather"})
    publish_handler.handler(event(body, token="sess_abc"))

    assert recorder.seen.token == "sess_abc"
    assert recorder.seen.bundle_id == "weather"
    assert recorder.seen.filename == "weather-1.0.0.agentpkg"
    assert recorder.seen.package == b"zip"


def test_a_bare_authorization_header_still_yields_the_token(recorder):
    request = event(multipart({"package": ("x.agentpkg", b"zip")}))
    request["headers"]["Authorization"] = "sess_no_scheme"
    publish_handler.handler(request)
    assert recorder.seen.token == "sess_no_scheme"


def test_headers_are_matched_case_insensitively(recorder):
    """ALB lowercases them; API Gateway and local curl do not."""
    request = event(multipart({"package": ("x.agentpkg", b"zip")}))
    request["headers"] = {k.lower(): v for k, v in request["headers"].items()}
    publish_handler.handler(request)
    assert recorder.seen.token == "tok"


# ────────────────────────────── responses ──────────────────────────────


def test_the_intakes_status_and_body_are_passed_through(monkeypatch):
    r = Recorder(status=409, message="bump your version")
    monkeypatch.setattr(publish_handler, "service", lambda: r)
    response = publish_handler.handler(event(multipart({"package": ("x.agentpkg", b"zip")})))
    assert response["statusCode"] == 409
    assert json.loads(response["body"])["message"] == "bump your version"
    assert response["headers"]["Content-Type"] == "application/json"


def test_a_held_index_lock_is_503_and_retryable_not_a_500(monkeypatch):
    class Locked:
        def submit(self, submission):
            raise TimeoutError("another publish is in progress")

    monkeypatch.setattr(publish_handler, "service", lambda: Locked())
    response = publish_handler.handler(event(multipart({"package": ("x.agentpkg", b"zip")})))
    assert response["statusCode"] == 503
    assert "in progress" in json.loads(response["body"])["message"]


def test_an_unexpected_failure_never_leaks_a_traceback(monkeypatch):
    class Boom:
        def submit(self, submission):
            raise RuntimeError("secret internal detail")

    monkeypatch.setattr(publish_handler, "service", lambda: Boom())
    response = publish_handler.handler(event(multipart({"package": ("x.agentpkg", b"zip")})))
    assert response["statusCode"] == 500
    body = json.loads(response["body"])
    assert "secret internal detail" not in body["message"]
    assert "Nothing was published" in body["message"]


# ────────────────────────────── the admin door ──────────────────────────────


class FakeAdmin:
    """Records calls; decisions (auth included) are the service's and are tested there."""

    def __init__(self):
        from agent_runtime.application.interfaces.publish_intake import IntakeResult

        self.calls = []
        self._result = IntakeResult(200, "admitted 1 creator(s).")

    def pending(self, token):
        self.calls.append(("pending", token))
        return None, [{"creator_id": "c-bob", "name": "Bob", "parked": []}]

    def admit(self, token, creator_ids=None):
        self.calls.append(("admit", token, list(creator_ids or [])))
        return self._result

    def revoke(self, token, creator_id):
        self.calls.append(("revoke", token, creator_id))
        return self._result


@pytest.fixture
def admin(monkeypatch):
    a = FakeAdmin()
    monkeypatch.setattr(publish_handler, "services", lambda: (None, a))
    return a


def admin_event(path, method="POST", body=None, token="admin-tok"):
    return {
        "path": path,
        "httpMethod": method,
        "headers": {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        "isBase64Encoded": False,
        "body": json.dumps(body or {}),
    }


def test_pending_is_a_get_that_lists_the_queue(admin):
    response = publish_handler.handler(admin_event("/registry/admin/pending", method="GET"))
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["pending"][0]["creator_id"] == "c-bob"
    assert admin.calls == [("pending", "admin-tok")]


def test_admit_posts_the_ids_and_relays_the_result(admin):
    response = publish_handler.handler(
        admin_event("/registry/admin/admit", body={"creator_ids": ["c-bob"]})
    )
    assert response["statusCode"] == 200
    assert "admitted" in json.loads(response["body"])["message"]
    assert admin.calls == [("admit", "admin-tok", ["c-bob"])]


def test_admit_accepts_the_single_id_shorthand(admin):
    publish_handler.handler(admin_event("/registry/admin/admit", body={"creator_id": "c-bob"}))
    assert admin.calls == [("admit", "admin-tok", ["c-bob"])]


def test_revoke_posts_the_id(admin):
    publish_handler.handler(admin_event("/registry/admin/revoke", body={"creator_id": "c-bob"}))
    assert admin.calls == [("revoke", "admin-tok", "c-bob")]


def test_admin_routes_reject_the_wrong_method(admin):
    assert publish_handler.handler(admin_event("/registry/admin/pending"))["statusCode"] == 405
    assert (
        publish_handler.handler(admin_event("/registry/admin/admit", method="GET"))["statusCode"]
        == 405
    )
    assert admin.calls == []


def test_admin_routes_survive_a_stage_prefix(admin):
    """Same suffix-matching rule as the publish route: ALBs and API Gateway stages prepend."""
    response = publish_handler.handler(
        admin_event("/prod/registry/admin/pending", method="GET")
    )
    assert response["statusCode"] == 200


def test_an_admin_failure_never_leaks_a_traceback(monkeypatch):
    class Boom:
        def pending(self, token):
            raise RuntimeError("secret internal detail")

    monkeypatch.setattr(publish_handler, "services", lambda: (None, Boom()))
    response = publish_handler.handler(admin_event("/registry/admin/pending", method="GET"))
    assert response["statusCode"] == 500
    body = json.loads(response["body"])
    assert "secret internal detail" not in body["message"]
    assert "unchanged" in body["message"]

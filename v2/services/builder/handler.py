"""Builder service — compiles ONE agent's window (app/ sources -> built ui/) per request.

WHY THIS EXISTS. `create_agent` and every code iteration in Agent Builder run `vite build`, and
on a hosted daemon that build ran INSIDE the daemon's container — where a node build's memory
peak is bigger than the whole task (512 MB), so the kernel killed the daemon mid-create and took
every user's socket down with it (exit 137, 2026-08-29). Builds belong in their own box: this
Lambda is that box, sized for one build and thrown away after.

STATELESS, BY CONSTRUCTION. The agent's files live on EFS with the daemon, before and after.
Per request this box downloads that agent's sources from the scratch bucket, compiles in /tmp,
uploads the built ui/ back to the bucket, and answers with the key. Nothing survives between
requests except the read-only skeleton dependencies baked into the image — which is the speed
trick: `node_modules` for the skeleton's package.json is ALREADY HERE, so the ordinary build is
vite-only. `npm install` runs only when an agent's package.json actually added something.

TRANSPORT IS S3 IN BOTH DIRECTIONS, not request/response bodies: the ALB caps Lambda bodies at
1 MB, and a built ui/ (fonts included) does not reliably fit. The JSON that does travel carries
keys, not files.

THE SAME FILE RUNS THREE WAYS, deliberately (see __main__): as the Lambda handler behind the
ALB, as a plain HTTP server for a desktop/dev box running the identical image (`SERVER_MODE`),
and as a one-shot CLI for poking a build locally. One build function, three front doors — the
cloud build environment is therefore reproducible on any machine with Docker.
"""

from __future__ import annotations

import hmac
import json
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

#: The baked skeleton: package.json + node_modules installed at IMAGE BUILD (Dockerfile), plus
#: a reference copy of the skeleton sources and their prebuilt ui/ (the Dockerfile's smoke build
#: keeps its output — it doubles as the canonical "a new agent's window before any edits").
SKELETON = Path(os.environ.get("BUILDER_SKELETON_DIR", "/opt/skeleton"))

#: Where sources arrive and results depart. On AWS the daemon's task role and this Lambda share
#: exactly this bucket and nothing else.
SCRATCH_BUCKET = os.environ.get("BUILDER_SCRATCH_BUCKET", "")

#: Shared secret the daemon presents (X-Internal-Key). Empty means OPEN — acceptable only for a
#: local/dev container, and the Dockerfile does not set one.
INTERNAL_KEY = os.environ.get("BUILDER_INTERNAL_KEY", "")

NPM_INSTALL_TIMEOUT_S = 240.0
VITE_BUILD_TIMEOUT_S = 300.0

#: Tail size for the log carried in the RESPONSE. The full log goes to S3 beside the result —
#: the tail exists so the agent loop can read the actual error without a second download.
LOG_TAIL_CHARS = 4000


class BuildRefused(Exception):
    """Request-shaped problems (bad zip, missing key) — the caller's fault, reported as 400."""


def _s3():
    import boto3  # imported lazily so the CLI/server modes work without AWS credentials

    return boto3.client("s3")


# ------------------------------------------------------------------ the build itself


def build_sources(sources_zip: Path, job_dir: Path) -> tuple[bool, str, Path]:
    """Compile one agent's window. Returns (ok, log, ui_dir).

    Layout inside job_dir mirrors an agent's own directory: sources unpack to app/ and vite's
    configured outDir ('../ui', vite.config.ts) lands the output at ui/ beside it — the same
    shape the daemon copies back onto EFS.
    """
    app_dir = job_dir / "app"
    app_dir.mkdir(parents=True)
    _unzip_guarded(sources_zip, app_dir)
    if not (app_dir / "package.json").exists():
        raise BuildRefused("sources zip has no package.json at its root — not an app directory")

    log: list[str] = []

    # DEPENDENCIES. The fast path is a symlink to the baked modules — zero copy, zero network.
    # An agent that ADDED deps gets a real install on top of a writable copy; the log says which
    # path ran, because "why was this build slow" must never be a mystery.
    if _deps_match_skeleton(app_dir / "package.json"):
        (app_dir / "node_modules").symlink_to(SKELETON / "node_modules")
        log.append("deps: baked skeleton modules (symlink)")
    else:
        log.append("deps: package.json differs from skeleton — copying modules + npm install")
        shutil.copytree(SKELETON / "node_modules", app_dir / "node_modules", symlinks=True)
        ok, out = _run(
            ["npm", "install", "--no-audit", "--no-fund"], app_dir, NPM_INSTALL_TIMEOUT_S
        )
        log.append(out)
        if not ok:
            return False, "\n".join(log), job_dir / "ui"

    ok, out = _run(["npm", "run", "build"], app_dir, VITE_BUILD_TIMEOUT_S)
    log.append(out)
    ui_dir = job_dir / "ui"
    if ok and not (ui_dir / "index.html").exists():
        ok = False
        log.append("build reported success but produced no ui/index.html — refusing the result")
    return ok, "\n".join(log), ui_dir


def _deps_match_skeleton(package_json: Path) -> bool:
    try:
        theirs = json.loads(package_json.read_text(encoding="utf-8"))
        ours = json.loads((SKELETON / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    keys = ("dependencies", "devDependencies")
    return all((theirs.get(k) or {}) == (ours.get(k) or {}) for k in keys)


def _run(cmd: list[str], cwd: Path, timeout: float) -> tuple[bool, str]:
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "CI": "1", "NO_COLOR": "1"},
        )
    except subprocess.TimeoutExpired:
        return False, f"$ {' '.join(cmd)}\ntimed out after {timeout:.0f}s"
    out = f"$ {' '.join(cmd)}\n{p.stdout or ''}{p.stderr or ''}"
    return p.returncode == 0, out


def _unzip_guarded(zip_path: Path, dest: Path) -> None:
    """Extract, refusing traversal. A zip is caller-supplied input; '../' in a member name must
    die here, not land a file outside the job directory."""
    with zipfile.ZipFile(zip_path) as z:
        root = dest.resolve()
        for member in z.infolist():
            target = (dest / member.filename).resolve()
            if not str(target).startswith(str(root) + os.sep) and target != root:
                raise BuildRefused(f"zip member escapes the job dir: {member.filename}")
        z.extractall(dest)


# ------------------------------------------------------------------ the S3 job wrapper


def presign_sources(params: dict) -> dict:
    """Hand the daemon a presigned PUT for its sources zip — the daemon carries no AWS SDK, so
    every S3 touch it makes rides a URL this function signs. Ten minutes is plenty: the daemon
    uploads immediately and asks for the build in the same breath."""
    if not SCRATCH_BUCKET:
        raise BuildRefused("BUILDER_SCRATCH_BUCKET is not configured")
    agent_id = str(params.get("agent_id") or "agent").strip() or "agent"
    key = f"builder/sources/{agent_id}-{int(time.time() * 1000)}.zip"
    url = _s3().generate_presigned_url(
        "put_object", Params={"Bucket": SCRATCH_BUCKET, "Key": key}, ExpiresIn=600
    )
    return {"ok": True, "sources_key": key, "put_url": url}


def run_job(params: dict) -> dict:
    """sources_key -> {ok, result_url?, log_key, log_tail}. The one job shape all three front
    doors share. `result_url` is a presigned GET — same no-SDK contract as presign_sources."""
    sources_key = str(params.get("sources_key") or "")
    if not sources_key:
        raise BuildRefused("sources_key is required")
    if not SCRATCH_BUCKET:
        raise BuildRefused("BUILDER_SCRATCH_BUCKET is not configured")
    agent_id = str(params.get("agent_id") or "agent").strip() or "agent"
    prefix = str(params.get("result_prefix") or "builder/results/")
    stamp = f"{agent_id}-{int(time.time() * 1000)}"

    s3 = _s3()
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="build-") as td:
        job_dir = Path(td)
        src = job_dir / "sources.zip"
        s3.download_file(SCRATCH_BUCKET, sources_key, str(src))
        ok, log, ui_dir = build_sources(src, job_dir)

        log_key = f"{prefix}{stamp}.log"
        s3.put_object(Bucket=SCRATCH_BUCKET, Key=log_key, Body=log.encode("utf-8"))
        out: dict = {"ok": ok, "log_key": log_key, "log_tail": log[-LOG_TAIL_CHARS:]}
        if ok:
            result = job_dir / "ui.zip"
            _zip_dir(ui_dir, result)
            result_key = f"{prefix}{stamp}.zip"
            s3.upload_file(str(result), SCRATCH_BUCKET, result_key)
            out["result_key"] = result_key
            out["result_url"] = s3.generate_presigned_url(
                "get_object", Params={"Bucket": SCRATCH_BUCKET, "Key": result_key}, ExpiresIn=600
            )
        return out


def _zip_dir(directory: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(directory.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(directory).as_posix())


# ------------------------------------------------------------------ front door 1: Lambda/ALB


def handler(event, context=None):  # noqa: ARG001 — Lambda signature
    """ALB proxy event (or a direct invoke carrying the job params) -> response."""
    # Direct invoke (aws lambda invoke / tests): the params ARE the event.
    if isinstance(event, dict) and ("sources_key" in event or event.get("op") == "presign"):
        return _guarded_job(event, headers={})

    headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}
    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64

        body = base64.b64decode(body).decode("utf-8", "replace")
    try:
        params = json.loads(body or "{}")
    except ValueError:
        return _http(400, {"ok": False, "error": "body is not JSON"})
    result = _guarded_job(params, headers)
    return _http(200 if result.get("ok") or "error" not in result else 400, result)


def _guarded_job(params: dict, headers: dict) -> dict:
    if INTERNAL_KEY:
        presented = str(headers.get("x-internal-key") or params.get("internal_key") or "")
        if not hmac.compare_digest(presented, INTERNAL_KEY):
            return {"ok": False, "error": "unauthorized"}
    try:
        if params.get("op") == "presign":
            return presign_sources(params)
        return run_job(params)
    except BuildRefused as e:
        return {"ok": False, "error": str(e)}


def _http(code: int, payload: dict) -> dict:
    body = json.dumps(payload)
    return {
        "statusCode": code,
        "statusDescription": f"{code} OK" if code == 200 else f"{code} Bad Request",
        "isBase64Encoded": False,
        "headers": {"Content-Type": "application/json"},
        "body": body,
    }


# ------------------------------------------------- front doors 2 + 3: local server / one-shot


def _serve_http(port: int) -> None:
    """The identical build box on a desktop or dev machine: `docker run -e SERVER_MODE=http`.
    Same job shape, no ALB, no Lambda — what makes a cloud build failure reproducible locally."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 — http.server API
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            try:
                params = json.loads(raw or b"{}")
            except ValueError:
                params = {}
            result = _guarded_job(params, {k.lower(): v for k, v in self.headers.items()})
            body = json.dumps(result).encode("utf-8")
            self.send_response(200 if result.get("ok") or "error" not in result else 400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):  # noqa: A003
            print(f"[builder] {fmt % args}")

    print(f"[builder] serving on :{port}")
    HTTPServer(("0.0.0.0", port), _Handler).serve_forever()


if __name__ == "__main__":
    import sys

    if os.environ.get("SERVER_MODE") == "http":
        _serve_http(int(os.environ.get("PORT", "4400")))
    elif len(sys.argv) == 3:
        # one-shot: python handler.py <sources.zip> <out-dir> — no S3, pure local build
        with tempfile.TemporaryDirectory(prefix="build-") as td:
            ok, log, ui = build_sources(Path(sys.argv[1]), Path(td))
            print(log)
            if ok:
                shutil.copytree(ui, Path(sys.argv[2]), dirs_exist_ok=True)
            sys.exit(0 if ok else 1)
    else:
        print("usage: SERVER_MODE=http python handler.py  |  python handler.py sources.zip out/")
        sys.exit(2)

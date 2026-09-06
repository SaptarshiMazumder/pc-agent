"""Executor service — runs ONE untrusted job per invocation, inside this Lambda's own microVM.

WHY THIS EXISTS. The daemon's plugin sandbox used to isolate untrusted tool calls in a child
PROCESS on the daemon's own box — same kernel, same filesystem, same network. The hosted rule is
stricter: untrusted code (a marketplace plugin's tools, an authored agent's private tools, a
shell command) never executes on the machine that holds every tenant's data. Each Lambda
invocation is its own Firecracker microVM; this handler is what runs inside it.

WHAT RUNS IN HERE IS THE SAME WORKER. The daemon-side subprocess backend spawns
`agent_runtime...sandbox.worker` and speaks JSON lines to it; this handler spawns the IDENTICAL
worker (the image is built FROM the daemon image, so the runtime environment is byte-identical)
and speaks the identical protocol. The one difference is where the host end of the channel is:
model/fetch frames cannot ride stdin across the network, so they are relayed through S3 "slots"
(below). worker.py, child_guard.py, the brokers and the wire format are all reused, unchanged.

TRANSPORT IS S3, NOT BODIES — same rule as the builder service next door: the ALB caps Lambda
bodies at 1 MB, and a workspace or a plugin folder does not fit. The daemon carries no AWS SDK,
so every S3 URL it touches is presigned here (`op: presign`).

THE SLOT CHANNEL (model/fetch brokering, daemon <-> this box, no daemon ingress needed):
the worker's `model_request` / `fetch_request` frames are answered by the DAEMON (the grant
check, the metering, the credential all live there — nothing in this box can make the call).
This box writes frame N to `executor/jobs/<job>/req-N.json` and polls `res-N.json`; the daemon
polls presigned GETs for req-N and PUTs the answer. Sequential by design: one outstanding
brokered call at a time, which is simple, ordered, and fits the deadline-pausing model the
subprocess backend already uses. Latency is one poll interval per call, paid only by tools that
actually phone home.

JOB KINDS:
  presign    -> job_id + presigned PUTs for the code/workspace zips + the broker slot URLs
  run        -> execute one plugin tool via the worker; answer result + changed-workspace zip
  enumerate  -> import a plugin and answer its TOOL SPECS (name/description/parameters/...),
                so the daemon can register untrusted tools without importing their code
  shell      -> run one command in the synced workspace (the hosted `exec` branch)

Front doors mirror services/builder/handler.py: ALB Lambda target, SERVER_MODE=http for a local
container, and direct invoke for tests.
"""

from __future__ import annotations

import hmac
import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

SCRATCH_BUCKET = os.environ.get("EXECUTOR_SCRATCH_BUCKET", "")
INTERNAL_KEY = os.environ.get("EXECUTOR_INTERNAL_KEY", "")

#: Broker slots minted per job. Sequential, one per brokered call; a tool that legitimately makes
#: more calls than this in ONE invocation is doing something the grant should have refused.
SLOT_COUNT = 128

#: How long a presigned URL lives. Generous: the whole job must fit inside it.
URL_TTL_S = 1800

#: Poll cadence for a broker response slot. The daemon polls requests at a similar rate, so a
#: brokered call costs ~half a second of channel overhead on top of its own work.
RES_POLL_S = 0.25

#: Ceiling on any zip this box accepts or produces. Oversize is an honest refusal, not a truncation.
MAX_ZIP_MB = int(os.environ.get("EXECUTOR_MAX_ZIP_MB", "256"))

#: Wall clock for a job whose payload does not set one (the daemon always does).
DEFAULT_TIMEOUT_S = 120.0

#: Directories never synced back even if changed — caches, bytecode.
_SKIP_SYNC_DIRS = frozenset({"__pycache__", ".pytest_cache", "node_modules"})


class JobRefused(Exception):
    """Request-shaped problems (bad zip, missing key, oversize) — reported as 400."""


def _s3():
    import boto3  # lazy so SERVER_MODE / direct-invoke tests run without AWS credentials
    from botocore.config import Config

    # Regional, virtual-host, SigV4 — same reasoning (and same live incident) as the builder:
    # a presigned URL signs the HOST, and a defaulted client mints URLs that 307-redirect.
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or ""
    kwargs: dict = {"config": Config(signature_version="s3v4", s3={"addressing_style": "virtual"})}
    if region:
        kwargs["region_name"] = region
        kwargs["endpoint_url"] = f"https://s3.{region}.amazonaws.com"
    return boto3.client("s3", **kwargs)


# ------------------------------------------------------------------ op: presign


def presign(params: dict) -> dict:
    """Mint one job's conveyor: upload URLs for whatever the daemon says it will send, plus the
    broker slot URLs the daemon will poll/answer during the run.

    `code_fingerprint`: content hash of the plugin/agent code tree. Code zips are CACHED by that
    hash (plugin code changes rarely; workspaces change every run), so the answer may be
    "already have it" — `code_put_url` is then absent and the daemon skips the upload."""
    if not SCRATCH_BUCKET:
        raise JobRefused("EXECUTOR_SCRATCH_BUCKET is not configured")
    s3 = _s3()
    job_id = f"{int(time.time() * 1000)}-{os.urandom(6).hex()}"
    out: dict = {"ok": True, "job_id": job_id}

    fingerprint = str(params.get("code_fingerprint") or "").strip()
    if fingerprint:
        code_key = f"executor/code/{fingerprint}.zip"
        out["code_key"] = code_key
        try:
            s3.head_object(Bucket=SCRATCH_BUCKET, Key=code_key)
            out["code_cached"] = True
        except Exception:  # noqa: BLE001 — any head failure just means "upload it"
            out["code_cached"] = False
            out["code_put_url"] = s3.generate_presigned_url(
                "put_object", Params={"Bucket": SCRATCH_BUCKET, "Key": code_key}, ExpiresIn=URL_TTL_S
            )

    if params.get("workspace"):
        ws_key = f"executor/jobs/{job_id}/workspace.zip"
        out["workspace_key"] = ws_key
        out["workspace_put_url"] = s3.generate_presigned_url(
            "put_object", Params={"Bucket": SCRATCH_BUCKET, "Key": ws_key}, ExpiresIn=URL_TTL_S
        )

    if params.get("broker"):
        req_urls, res_urls = [], []
        for n in range(SLOT_COUNT):
            base = f"executor/jobs/{job_id}"
            req_urls.append(s3.generate_presigned_url(
                "get_object", Params={"Bucket": SCRATCH_BUCKET, "Key": f"{base}/req-{n}.json"},
                ExpiresIn=URL_TTL_S,
            ))
            res_urls.append(s3.generate_presigned_url(
                "put_object", Params={"Bucket": SCRATCH_BUCKET, "Key": f"{base}/res-{n}.json"},
                ExpiresIn=URL_TTL_S,
            ))
        out["broker_req_urls"] = req_urls
        out["broker_res_urls"] = res_urls
    return out


# ------------------------------------------------------------------ op: run


def run_job(params: dict) -> dict:
    """Execute one plugin tool call via the shared worker, inside this microVM.

    The daemon sends the SAME job dict the subprocess backend would write to a child's stdin,
    with two riders: a `sync` table naming which job fields are host paths that were shipped as
    zips (so this side can remap them under /tmp), and the S3 keys those zips sit at."""
    job = params.get("job") or {}
    if not job.get("entry") or not job.get("tool_name"):
        raise JobRefused("run needs job.entry and job.tool_name")
    timeout = float(params.get("timeout_s") or (job.get("grant") or {}).get("timeout_s") or DEFAULT_TIMEOUT_S)

    with tempfile.TemporaryDirectory(dir="/tmp", prefix="exec-") as td:
        root = Path(td)
        mapping = _materialise(params, root)
        job = _remap_job(job, mapping, root)

        frames, stderr_text, code = _pump_worker(job, params, timeout, cwd=mapping.get("ws") or td)

        result = next((f for f in frames if f.get("t") == "result"), None)
        out: dict = {
            "ok": result is not None and not result.get("isError"),
            "result": result,
            "updates": [f for f in frames if f.get("t") == "update"][-20:],
            "stderr_tail": stderr_text[-4000:],
            "exit_code": code,
        }
        if result is None:
            out["error"] = f"the worker exited (code {code}) without a result"
        ws_local = mapping.get("ws")
        if ws_local:
            changed_key = _upload_changes(params, Path(ws_local), mapping["ws_manifest"])
            if changed_key:
                s3 = _s3()
                out["changes_key"] = changed_key
                out["changes_url"] = s3.generate_presigned_url(
                    "get_object", Params={"Bucket": SCRATCH_BUCKET, "Key": changed_key},
                    ExpiresIn=URL_TTL_S,
                )
        return out


def enumerate_job(params: dict) -> dict:
    """Import one plugin INSIDE the microVM and answer its tool specs — how the daemon registers
    untrusted tools without ever importing their module (import executes code) in its process."""
    job = dict(params.get("job") or {})
    if not job.get("entry"):
        raise JobRefused("enumerate needs job.entry")
    job["kind"] = "enumerate"
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="enum-") as td:
        root = Path(td)
        mapping = _materialise(params, root)
        job = _remap_job(job, mapping, root)
        frames, stderr_text, code = _pump_worker(job, params, timeout_s(params, 60.0), cwd=td)
        result = next((f for f in frames if f.get("t") == "result"), None)
        if result is None or result.get("isError"):
            text = _result_text(result) or f"worker exited (code {code})"
            return {"ok": False, "error": text, "stderr_tail": stderr_text[-2000:]}
        return {"ok": True, "specs": (result.get("details") or {}).get("specs") or []}


def shell_job(params: dict) -> dict:
    """One command in the synced workspace — the hosted branch of the `exec` tool. The command is
    the CALLER's own text (the builder's shell), not plugin code; what this box provides is the
    microVM: nothing here holds keys, tenants, or the daemon's memory."""
    command = str(params.get("command") or "").strip()
    if not command:
        raise JobRefused("shell needs a command")
    timeout = timeout_s(params, 300.0)
    with tempfile.TemporaryDirectory(dir="/tmp", prefix="sh-") as td:
        root = Path(td)
        mapping = _materialise(params, root)
        cwd = mapping.get("ws") or str(root)
        env = {**os.environ, "HOME": cwd, "CI": "1", "NO_COLOR": "1"}
        # The COMMAND never inherits this box's AWS authority: the executor's role can touch the
        # shared scratch bucket, where OTHER jobs' workspaces ride — a shell that kept these could
        # read across jobs. The handler brokers S3 itself; the command gets none of it.
        for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
                     "EXECUTOR_INTERNAL_KEY", "EXECUTOR_SCRATCH_BUCKET"):
            env.pop(name, None)
        env.update({str(k): str(v) for k, v in (params.get("env") or {}).items()})
        try:
            p = subprocess.run(
                command, shell=True, cwd=cwd, capture_output=True, text=True,
                timeout=timeout, env=env,
            )
            output = (p.stdout or "") + (p.stderr or "")
            code = p.returncode
        except subprocess.TimeoutExpired as e:
            output = ((e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")) \
                + f"\n[timed out after {timeout:.0f}s]"
            code = -1
        out: dict = {"ok": code == 0, "exit_code": code, "output": output[-50_000:]}
        if mapping.get("ws"):
            changed_key = _upload_changes(params, Path(mapping["ws"]), mapping["ws_manifest"])
            if changed_key:
                out["changes_key"] = changed_key
                out["changes_url"] = _s3().generate_presigned_url(
                    "get_object", Params={"Bucket": SCRATCH_BUCKET, "Key": changed_key},
                    ExpiresIn=URL_TTL_S,
                )
        return out


def timeout_s(params: dict, default: float) -> float:
    try:
        return float(params.get("timeout_s") or default)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------ materialise + remap


def _materialise(params: dict, root: Path) -> dict:
    """Download and unzip whatever the daemon shipped. Returns the mapping table:
    { '<sync id>': '<local path>', 'ws': <workspace local or ''>, 'ws_manifest': {...} }."""
    s3 = _s3() if (params.get("code_key") or params.get("workspace_key")) else None
    mapping: dict = {"ws": "", "ws_manifest": {}}

    code_key = str(params.get("code_key") or "")
    if code_key:
        code_dir = root / "code"
        _fetch_zip(s3, code_key, code_dir)
        mapping["code"] = str(code_dir)

    ws_key = str(params.get("workspace_key") or "")
    if ws_key:
        ws_dir = root / "ws"
        _fetch_zip(s3, ws_key, ws_dir)
        mapping["ws"] = str(ws_dir)
        mapping["ws_manifest"] = _manifest(ws_dir)

    tmp = root / "tmp"
    tmp.mkdir(exist_ok=True)
    mapping["tmp"] = str(tmp)
    return mapping


def _remap_job(job: dict, mapping: dict, root: Path) -> dict:
    """Rewrite the job's host paths onto this box's copies, using the daemon's `sync` table:
    each entry says which JOB FIELD holds a path and which synced tree (by relative subdir of the
    code zip, or the workspace) it now lives in."""
    job = json.loads(json.dumps(job))  # deep copy; the job is small (paths + params + grant)
    code_root = mapping.get("code") or str(root / "code")
    ws_root = mapping.get("ws") or ""

    def to_local(entry: dict) -> str:
        kind = entry.get("in") or "code"
        rel = str(entry.get("rel") or "").strip("/").replace("\\", "/")
        base = ws_root if kind == "ws" else code_root
        return str(Path(base) / rel) if rel else str(base)

    for entry in job.pop("_sync", []) or []:
        field = str(entry.get("field") or "")
        local = to_local(entry)
        if field == "plugin_root":
            job["plugin_root"] = local
        elif field == "read_path":
            grant = job.setdefault("grant", {})
            grant["read_paths"] = [*grant.get("read_paths", []), local]
        elif field == "agent_dir":
            job["_agent_dir_local"] = local

    grant = job.setdefault("grant", {})
    if ws_root:
        grant["fs_paths"] = [ws_root]
        ctx = job.setdefault("ctx", {})
        if ctx.get("workspace"):
            ctx["workspace"] = ws_root
        # The tenant fence's host paths are meaningless here; the grant is the enforcement.
        ctx["read_roots"] = []
        ctx["write_clamp"] = []
    else:
        grant["fs_paths"] = []
    # Host-side deny paths name daemon files that do not exist in this box; the box's own
    # environment carries nothing sensitive, so the list resets rather than travels.
    job["deny_paths"] = []
    job["temp_dir"] = mapping.get("tmp") or str(root / "tmp")
    # read_paths the daemon did not ship are host paths — drop them so the guard's log stays
    # truthful about what was actually reachable.
    grant["read_paths"] = [p for p in grant.get("read_paths", []) if str(p).startswith(str(root))]
    return job


# ------------------------------------------------------------------ the worker pump


def _pump_worker(job: dict, params: dict, timeout: float, cwd: str) -> tuple[list[dict], str, int]:
    """Spawn the SAME sandbox worker the subprocess backend uses and speak its protocol; broker
    frames relay through the S3 slots. Returns (frames, stderr, exit_code).

    The deadline pauses while a brokered call is outstanding — identical policy to the
    subprocess backend's _Deadline, because a model call the daemon is making on the tool's
    behalf is not the tool spending its budget."""
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    env.pop("AWS_ACCESS_KEY_ID", None)  # the WORKER (untrusted code) gets no AWS credentials —
    env.pop("AWS_SECRET_ACCESS_KEY", None)  # this handler brokers S3; the guard polices the rest
    env.pop("AWS_SESSION_TOKEN", None)
    env.pop("EXECUTOR_INTERNAL_KEY", None)
    proc = subprocess.Popen(
        [sys.executable, "-P", "-s", "-B", "-m",
         "agent_runtime.infrastructure.tools.sandbox.worker"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=cwd, env=env, text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    frames: list[dict] = []
    slot = 0
    started = time.monotonic()
    paused_total = 0.0
    try:
        proc.stdin.write(json.dumps(job) + "\n")
        proc.stdin.flush()
        while True:
            if (time.monotonic() - started - paused_total) > timeout:
                proc.kill()
                frames.append({"t": "result", "isError": True, "content": [{
                    "type": "text",
                    "text": f"microvm: the tool exceeded its {timeout:.0f}s limit and was stopped.",
                }], "artifacts": []})
                break
            line = proc.stdout.readline()
            if not line:
                break
            try:
                frame = json.loads(line)
            except ValueError:
                continue
            kind = frame.get("t")
            if kind in ("model_request", "fetch_request"):
                pause_at = time.monotonic()
                answer = _broker_roundtrip(params, slot, frame, deadline_s=URL_TTL_S)
                paused_total += time.monotonic() - pause_at
                slot += 1
                try:
                    proc.stdin.write(json.dumps(answer) + "\n")
                    proc.stdin.flush()
                except (BrokenPipeError, OSError):
                    break
            else:
                frames.append(frame)
                if kind == "result":
                    break
    finally:
        try:
            proc.kill()
        except OSError:
            pass
        stderr_text = ""
        try:
            _out, stderr_text = proc.communicate(timeout=10)
        except (subprocess.TimeoutExpired, ValueError, OSError):
            pass
    return frames, stderr_text or "", proc.returncode if proc.returncode is not None else -1


def _broker_roundtrip(params: dict, slot: int, frame: dict, deadline_s: float) -> dict:
    """One brokered call: write req-<slot>, poll res-<slot> until the daemon answers."""
    job_id = str(params.get("job_id") or "")
    if str(params.get("op") or "") != "run":
        # Only a `run` has the daemon polling the slots. An enumerate whose plugin phones a
        # model at import time would otherwise write requests into a channel nobody reads and
        # hang on the poll — answer it immediately instead.
        return _broker_error(frame, "microvm: no broker channel outside a run job")
    if not job_id or slot >= SLOT_COUNT:
        return _broker_error(frame, "microvm: no broker channel for this call")
    s3 = _s3()
    base = f"executor/jobs/{job_id}"
    s3.put_object(Bucket=SCRATCH_BUCKET, Key=f"{base}/req-{slot}.json",
                  Body=json.dumps(frame).encode("utf-8"))
    waited = 0.0
    while waited < deadline_s:
        try:
            obj = s3.get_object(Bucket=SCRATCH_BUCKET, Key=f"{base}/res-{slot}.json")
            return json.loads(obj["Body"].read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001 — NoSuchKey until the daemon answers
            time.sleep(RES_POLL_S)
            waited += RES_POLL_S
    return _broker_error(frame, "microvm: the daemon did not answer the brokered call in time")


def _broker_error(frame: dict, message: str) -> dict:
    kind = "model_response" if frame.get("t") == "model_request" else "fetch_response"
    out = {"t": kind, "id": str(frame.get("id") or ""), "error": message}
    if kind == "model_response":
        out.update({"ok": False, "text": ""})
    else:
        out.update({"status": 0, "headers": {}, "text": "", "url": ""})
    return out


# ------------------------------------------------------------------ zips + manifests


def _fetch_zip(s3, key: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir="/tmp", suffix=".zip", delete=False) as f:
        tmp = Path(f.name)
    try:
        s3.download_file(SCRATCH_BUCKET, key, str(tmp))
        if tmp.stat().st_size > MAX_ZIP_MB * 1024 * 1024:
            raise JobRefused(f"zip {key} exceeds the {MAX_ZIP_MB} MB ceiling")
        _unzip_guarded(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def _unzip_guarded(zip_path: Path, dest: Path) -> None:
    with zipfile.ZipFile(zip_path) as z:
        root = dest.resolve()
        for member in z.infolist():
            target = (dest / member.filename).resolve()
            if not str(target).startswith(str(root) + os.sep) and target != root:
                raise JobRefused(f"zip member escapes the job dir: {member.filename}")
        z.extractall(dest)


def _manifest(directory: Path) -> dict:
    out: dict = {}
    for f in directory.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(directory).as_posix()
        if any(part in _SKIP_SYNC_DIRS for part in Path(rel).parts):
            continue
        st = f.stat()
        out[rel] = [st.st_size, st.st_mtime_ns]
    return out


def _upload_changes(params: dict, ws_dir: Path, before: dict) -> str:
    """Zip files the job created or modified and put them beside it. Deletions are NOT propagated
    (reported implicitly by their absence here) — an untrusted tool must not be able to erase a
    workspace through the sync channel."""
    after = _manifest(ws_dir)
    changed = [rel for rel, sig in after.items() if before.get(rel) != sig]
    if not changed:
        return ""
    buf_path = Path(tempfile.mkstemp(dir="/tmp", suffix=".zip")[1])
    try:
        with zipfile.ZipFile(buf_path, "w", zipfile.ZIP_DEFLATED) as z:
            for rel in changed:
                z.write(ws_dir / rel, rel)
        if buf_path.stat().st_size > MAX_ZIP_MB * 1024 * 1024:
            raise JobRefused(
                f"the job changed {len(changed)} file(s) totalling more than {MAX_ZIP_MB} MB — "
                "too large to sync back"
            )
        key = f"executor/jobs/{params.get('job_id') or 'job'}/changes.zip"
        _s3().upload_file(str(buf_path), SCRATCH_BUCKET, key)
        return key
    finally:
        buf_path.unlink(missing_ok=True)


def _result_text(result: dict | None) -> str:
    if not result:
        return ""
    return " ".join(
        str(b.get("text") or "") for b in (result.get("content") or []) if isinstance(b, dict)
    ).strip()


# ------------------------------------------------------------------ front doors


_OPS = {"presign": presign, "run": run_job, "enumerate": enumerate_job, "shell": shell_job}


def _guarded_job(params: dict, headers: dict) -> dict:
    if INTERNAL_KEY:
        presented = str(headers.get("x-internal-key") or params.get("internal_key") or "")
        if not hmac.compare_digest(presented, INTERNAL_KEY):
            return {"ok": False, "error": "unauthorized"}
    op = str(params.get("op") or "run")
    fn = _OPS.get(op)
    if fn is None:
        return {"ok": False, "error": f"unknown op '{op}'"}
    try:
        return fn(params)
    except JobRefused as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001 — the daemon needs the reason, not a 502
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def handler(event, context=None):  # noqa: ARG001 — Lambda signature
    if isinstance(event, dict) and "op" in event and "headers" not in event:
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


def _http(code: int, payload: dict) -> dict:
    return {
        "statusCode": code,
        "statusDescription": f"{code} OK" if code == 200 else f"{code} Bad Request",
        "isBase64Encoded": False,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }


def _serve_http(port: int) -> None:
    """The identical box on a dev machine: `docker run -e SERVER_MODE=http` — what makes a cloud
    sandbox failure reproducible locally, same as the builder."""
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
            print(f"[executor] {fmt % args}")

    print(f"[executor] serving on :{port}")
    HTTPServer(("0.0.0.0", port), _Handler).serve_forever()


if __name__ == "__main__":
    if os.environ.get("SERVER_MODE") == "http":
        _serve_http(int(os.environ.get("PORT", "4500")))
    else:
        print("usage: SERVER_MODE=http python handler.py")
        sys.exit(2)

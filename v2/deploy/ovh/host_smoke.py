"""Real Linux/runsc + R2 smoke. Run inside the trusted executor, never the daemon.

  docker compose exec -T executor python - < host_smoke.py

Uses the service's existing scratch credentials, never prints them/signed URLs.
No accounts, billing or registry mutation. Temporary objects use scratch lifecycle.
"""

import io
import json
import os
import shlex
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

from services.common.docker_dispatch import cancel, control, dispatch
from services.common.object_storage import s3_client


def archive(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as output:
        for name, data in files.items():
            output.writestr(name, data)
    return buffer.getvalue()


def put(url, data):
    request = urllib.request.Request(url, data=data, method="PUT")
    with urllib.request.urlopen(request, timeout=60):
        pass


def main():
    os.environ.update(json.loads(Path(os.environ["SERVICE_SECRETS_FILE"]).read_text()))
    s3, bucket = s3_client(), os.environ["EXECUTOR_SCRATCH_BUCKET"]
    owner = "f" * 64
    job = "smoke-" + uuid.uuid4().hex
    workspace = f"executor/jobs/{job}/workspace.zip"
    s3.put_object(Bucket=bucket, Key=workspace, Body=archive({"hello.txt": "isolated"}))
    script = """
import asyncio, os, socket, urllib.request
from pathlib import Path
assert 'gvisor' in Path('/proc/version').read_text().lower(), 'runsc kernel expected'
assert not Path('/var/run/docker.sock').exists()
assert not Path('/run/service-secrets').exists()
for key in ('DATABASE_URL','AGENTD_STATE_KEK','AGENTD_IDENTITY_KEK','PUBLISH_KEY_KEK',
            'RUNTIME_CONTROL_KEY','PLATFORM_CONTROL_KEY','AWS_SECRET_ACCESS_KEY',
            'OBJECT_STORAGE_SECRET_ACCESS_KEY','GEMINI_API_KEY'):
    assert not os.environ.get(key), 'credential leaked: '+key
for target in ('http://169.254.169.254/', 'http://127.0.0.1/', 'http://accounts:4100/health'):
    try:
        urllib.request.urlopen(target, timeout=3)
    except Exception:
        pass
    else:
        raise AssertionError('private egress reachable')
gateways = os.environ['SANDBOX_HOST_GATEWAYS'].split(',')
assert all(gateways), 'host gateway inspection required'
for gateway in gateways:
    for port in (80, 443, 4100, 8787):
        try:
            connection = socket.create_connection((gateway, port), timeout=2)
        except OSError:
            pass
        else:
            connection.close()
            raise AssertionError('host gateway reachable despite firewall')
try:
    connection = socket.create_connection(('1.1.1.1', 443), timeout=2)
except OSError:
    pass
else:
    connection.close()
    raise AssertionError('direct internet route bypasses proxy')
async def browser():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True, proxy={'server':os.environ['HTTP_PROXY']})
        page = await b.new_page()
        await page.goto('https://example.com', wait_until='domcontentloaded', timeout=45000)
        assert 'Example Domain' in await page.title()
        await b.close()
asyncio.run(browser())
Path('result.txt').write_text('success')
print('isolated shell, public proxy and Chromium passed')
"""
    result = dispatch(
        {
            "op": "shell",
            "job_id": job,
            "tenant_owner": owner,
            "workspace_key": workspace,
            "timeout_s": 120,
            "command": "python3 -c " + shlex.quote(script),
        },
        s3,
        bucket,
    )
    if not result.get("ok") or not result.get("changes_url"):
        raise RuntimeError("isolated shell/browser smoke failed; inspect bounded worker logs")
    print("Real gVisor shell, Chromium, credential isolation and workspace sync passed")
    source = f"builder/sources/smoke-{uuid.uuid4().hex}.zip"
    s3.put_object(
        Bucket=bucket,
        Key=source,
        Body=archive(
            {
                "package.json": json.dumps(
                    {"name": "smoke", "version": "1.0.0", "scripts": {"build": "node build.js"}}
                ),
                "build.js": "const fs=require('fs');fs.mkdirSync('../ui',{recursive:true});fs.writeFileSync('../ui/index.html','smoke')",
            }
        ),
    )
    built = dispatch(
        {
            "job_id": "build-" + uuid.uuid4().hex,
            "tenant_owner": owner,
            "sources_key": source,
            "timeout_s": 600,
        },
        s3,
        bucket,
        kind="builder",
    )
    if not built.get("ok") or not built.get("result_url"):
        raise RuntimeError("isolated Node build failed; inspect bounded build logs")
    print("Real gVisor Node build and artifact transport passed")
    # Start a persistent browser task and exercise its object-capability RPC.
    session = dispatch(
        {
            "op": "session",
            "session_kind": "browser",
            "config": {},
            "host_workspace": "/smoke",
            "tenant_owner": owner,
        },
        s3,
        bucket,
    )
    try:
        put(session["workspace_put_url"], archive({"hello.txt": "smoke"}))
        put(
            session["rpc_put_urls"][0],
            json.dumps(
                {
                    "method": "fetch",
                    "workspace": True,
                    "arguments": {"url": "https://example.com", "max_chars": 2000},
                }
            ).encode(),
        )
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(session["rpc_get_urls"][0], timeout=10) as response:
                    answer = json.load(response)
                if not answer.get("ok"):
                    raise RuntimeError("persistent browser RPC failed")
                break
            except urllib.error.HTTPError as exc:
                if exc.code not in (403, 404):
                    raise
            time.sleep(1)
        else:
            raise TimeoutError("persistent browser RPC timed out")
    finally:
        cancel({"job_id": session["job_id"], "tenant_owner": owner}, s3, bucket)
    assert not control("/jobs/status", {"job_id": session["job_id"], "owner": owner})["running"]
    print("Real persistent browser RPC and cancellation passed")


if __name__ == "__main__":
    main()

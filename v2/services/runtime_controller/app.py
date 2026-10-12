"""Private control plane for disposable gVisor jobs on one production host.

Only this trusted service mounts Docker's socket. Job images, resource budgets and
networks are operator configuration, never fields accepted from a tenant.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

PROJECT = "agentd-ovh"
LABEL = "agentd.runtime"


def serialized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)

    return wrapped


class Controller:
    def __init__(self, engine, db_path, images, egress_network, object_host):
        self.engine, self.path, self.images = engine, str(db_path), images
        self.egress, self.object_host = egress_network, object_host
        self.lock = threading.RLock()
        if "runsc" not in engine.info().get("Runtimes", {}):
            raise RuntimeError("gVisor runsc is required; execution refused")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, owner TEXT NOT NULL, "
                "expires INTEGER NOT NULL, running INTEGER NOT NULL)"
            )

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def identity(data):
        job, owner = data.get("job_id", ""), data.get("owner", "")
        if not isinstance(job, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", job):
            raise ValueError("invalid job identity")
        if not isinstance(owner, str) or not re.fullmatch(r"[a-f0-9]{64}", owner):
            raise ValueError("verified runtime owner required")
        return job, owner

    def names(self, job):
        suffix = hashlib.sha256(job.encode()).hexdigest()[:20]
        return f"adjob-{suffix}", f"adproxy-{suffix}", f"adnet-{suffix}"

    def owned(self, job, owner):
        with self.db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
        if row and not hmac.compare_digest(row["owner"], owner):
            raise PermissionError("job belongs to another owner")
        return row

    @serialized
    def start(self, data):
        job, owner = self.identity(data)
        kind = data.get("kind")
        now = int(time.time())
        expires = int(data.get("expires", 0))
        if kind not in self.images or not now < expires <= now + 1500:
            raise ValueError("invalid job configuration")
        for field in ("input_url", "output_url"):
            url = urlsplit(str(data.get(field) or ""))
            if (
                url.scheme != "https"
                or url.hostname != self.object_host
                or url.port not in (None, 443)
                or url.username
                or url.password
                or url.fragment
            ):
                raise ValueError("invalid object capability")
        self.reap()
        # One shared slot on the cheapest host. Atomic admission includes startup.
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM jobs WHERE id=?", (job,)).fetchone():
                raise ValueError("job identity already used")
            if db.execute("SELECT 1 FROM jobs WHERE running=1").fetchone():
                raise OverflowError(
                    "sandbox busy; retry after the current build/tool/browser session finishes"
                )
            db.execute("INSERT INTO jobs VALUES (?,?,?,1)", (job, owner, expires))
        container_name, proxy_name, network_name = self.names(job)
        labels = {LABEL: PROJECT, "agentd.job": job, "agentd.expires": str(expires)}
        options = dict(
            runtime="runsc",
            user="1000:1000",
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            pids_limit=256,
            mem_limit="2g",
            memswap_limit="2g",
            nano_cpus=1_000_000_000,
            tmpfs={
                "/tmp": "rw,nosuid,nodev,size=1g,uid=1000,gid=1000",
                "/home/agentd": "rw,nosuid,nodev,size=64m,uid=1000,gid=1000",
            },
            shm_size="128m",
            labels=labels,
            log_config={"Type": "local", "Config": {"max-size": "10m", "max-file": "2"}},
        )
        try:
            net = self.engine.networks.create(
                network_name,
                internal=True,
                labels=labels,
                driver="bridge",
                options={"com.docker.network.bridge.name": "adjob" + network_name[-8:]},
            )
            proxy_options = {
                **options,
                "mem_limit": "128m",
                "memswap_limit": "128m",
                "nano_cpus": 250_000_000,
                "pids_limit": 64,
            }
            proxy = self.engine.containers.create(
                self.images["executor"],
                name=proxy_name,
                entrypoint=["python", "/var/task/egress_proxy.py"],
                command=[],
                network=self.egress,
                **proxy_options,
            )
            net.connect(proxy, aliases=["public-egress"])
            proxy.start()
            env = {
                "SANDBOX_INPUT_URL": data["input_url"],
                "SANDBOX_OUTPUT_URL": data["output_url"],
                "HTTP_PROXY": "http://public-egress:8080",
                "HTTPS_PROXY": "http://public-egress:8080",
                "http_proxy": "http://public-egress:8080",
                "https_proxy": "http://public-egress:8080",
                "NO_PROXY": "",
                "no_proxy": "",
                "HOME": "/home/agentd",
                "PYTHONUNBUFFERED": "1",
                "AGENTD_ISOLATED_TASK": "1",
                "PLAYWRIGHT_BROWSERS_PATH": "/opt/ms-playwright",
            }
            gateways = [
                config.get("Gateway")
                for config in net.attrs.get("IPAM", {}).get("Config", [])
                if config.get("Gateway")
            ]
            env["SANDBOX_HOST_GATEWAYS"] = ",".join(gateways)
            container = self.engine.containers.create(
                self.images[kind],
                name=container_name,
                entrypoint=["python3", "/var/task/fargate_runner.py"],
                command=[],
                network=network_name,
                environment=env,
                **options,
            )
            container.start()
        except BaseException:
            self.cleanup(job)
            raise
        return {"ok": True, "job_id": job, "expires_at": expires}

    @serialized
    def cleanup(self, job):
        container, proxy, network = self.names(job)
        for name in (container, proxy):
            try:
                obj = self.engine.containers.get(name)
            except Exception as exc:
                if getattr(exc, "status_code", None) == 404:
                    continue
                raise
            if obj.labels.get(LABEL) != PROJECT or obj.labels.get("agentd.job") != job:
                raise PermissionError("refusing to remove a foreign container")
            obj.remove(force=True)
        try:
            net = self.engine.networks.get(network)
        except Exception as exc:
            if getattr(exc, "status_code", None) != 404:
                raise
        else:
            if net.attrs.get("Labels", {}).get(LABEL) != PROJECT:
                raise PermissionError("refusing to remove a foreign network")
            net.remove()
        with self.db() as db:
            db.execute("UPDATE jobs SET running=0 WHERE id=?", (job,))

    @serialized
    def status(self, data):
        job, owner = self.identity(data)
        row = self.owned(job, owner)
        if not row or not row["running"]:
            return {"ok": True, "running": False}
        if row["expires"] <= time.time():
            self.cleanup(job)
            return {"ok": True, "running": False}
        try:
            obj = self.engine.containers.get(self.names(job)[0])
        except Exception as exc:
            if getattr(exc, "status_code", None) != 404:
                raise
            # Admission covers startup: an absent container is not proof of completion.
            return {"ok": True, "running": True}
        running = obj.status in ("created", "running", "restarting")
        if not running:
            self.cleanup(job)
        return {"ok": True, "running": running}

    @serialized
    def cancel(self, data):
        job, owner = self.identity(data)
        if self.owned(job, owner):
            self.cleanup(job)
        else:
            # A cancellation racing admission must prevent that job from starting later.
            with self.db() as db:
                db.execute(
                    "INSERT OR IGNORE INTO jobs VALUES (?,?,?,0)", (job, owner, int(time.time()))
                )
        return {"ok": True, "job_id": job}

    @serialized
    def reap(self):
        # Labels also cover resources left behind by a controller crash during creation.
        jobs = set()
        for obj in self.engine.containers.list(all=True, filters={"label": f"{LABEL}={PROJECT}"}):
            labels = obj.labels
            if int(labels.get("agentd.expires", 0)) <= time.time() or (
                obj.name.startswith("adjob-")
                and obj.status not in ("created", "running", "restarting")
            ):
                jobs.add(labels.get("agentd.job"))
        for obj in self.engine.networks.list(filters={"label": f"{LABEL}={PROJECT}"}):
            labels = obj.attrs.get("Labels", {})
            if int(labels.get("agentd.expires", 0)) <= time.time():
                jobs.add(labels.get("agentd.job"))
        with self.db() as db:
            jobs.update(
                row[0]
                for row in db.execute(
                    "SELECT id FROM jobs WHERE running=1 AND expires<=?", (int(time.time()),)
                )
            )
        for job in jobs:
            if isinstance(job, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", job):
                self.cleanup(job)
        with self.db() as db:
            db.execute(
                "DELETE FROM jobs WHERE running=0 AND expires<?", (int(time.time()) - 172800,)
            )


class SecretStore:
    def __init__(self, engine, root):
        self.engine, self.root = engine, Path(root)
        self.lock = threading.Lock()
        self.mapping = json.loads((self.root / "consumers.json").read_text())

    def metadata(self):
        keys = []
        for name, consumers in sorted(self.mapping.items()):
            values = [
                json.loads((self.root / service / "env.json").read_text()).get(name, "")
                for service in consumers
            ]
            keys.append(
                {
                    "name": name,
                    "set": all(bool(v) and v != "REPLACE_ME" for v in values),
                    "placeholder": any(v == "REPLACE_ME" for v in values),
                    "consumers": consumers,
                }
            )
        return {"configured": True, "id": "OVH service secret files", "keys": keys}

    def update(self, name, value):
        if (
            name not in self.mapping
            or not isinstance(value, str)
            or not value
            or len(value) > 16384
        ):
            raise ValueError("only configured provider/OAuth/payment credentials may be changed")
        consumers = self.mapping[name]
        with self.lock:
            for service in consumers:
                path = self.root / service / "env.json"
                document = json.loads(path.read_text())
                document[name] = value
                temp = path.with_suffix(".new")
                with temp.open("w") as stream:
                    json.dump(document, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temp, 0o600)
                if hasattr(os, "chown"):
                    os.chown(temp, 1000, 1000)
                os.replace(temp, path)
        return consumers

    def restart(self, consumers):
        for service in consumers:
            containers = self.engine.containers.list(
                filters={
                    "label": [
                        "com.docker.compose.project=agentd-prod",
                        f"com.docker.compose.service={service}",
                    ]
                }
            )
            for container in containers:
                container.restart(timeout=150 if service == "daemon" else 30)


def configured_controller():
    import docker

    host = urlsplit(os.environ["OBJECT_STORAGE_ENDPOINT"]).hostname
    return Controller(
        docker.from_env(),
        os.environ.get("RUNTIME_DB", "/state/runtime.db"),
        {kind: os.environ[kind.upper() + "_IMAGE"] for kind in ("executor", "builder")},
        os.environ["SANDBOX_EGRESS_NETWORK"],
        host,
    )


def create_app(controller=None):
    key = os.environ.get("RUNTIME_CONTROL_KEY", "")
    if len(key) < 32:
        raise RuntimeError("a strong runtime control key is required")

    @asynccontextmanager
    async def lifespan(app):
        app.state.controller = controller or configured_controller()
        root = os.environ.get("PLATFORM_SECRETS_ROOT")
        app.state.secrets = SecretStore(app.state.controller.engine, root) if root else None

        async def reaper():
            while True:
                try:
                    await asyncio.to_thread(app.state.controller.reap)
                except Exception:
                    # Do not leak signed URLs/credentials from SDK exception text.
                    print("runtime reaper failed; host reaper must also be installed", flush=True)
                await asyncio.sleep(15)

        task = asyncio.create_task(reaper())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/jobs/{action}")
    async def jobs(action: str, request: Request):
        if not hmac.compare_digest(request.headers.get("x-control-key", ""), key):
            raise HTTPException(401, "unauthorized")
        raw = await request.body()
        if len(raw) > 32768:
            raise HTTPException(413, "request too large")
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or action not in ("start", "status", "cancel"):
                raise ValueError("invalid action")
            return await asyncio.to_thread(getattr(app.state.controller, action), data)
        except PermissionError:
            raise HTTPException(403, "owner mismatch") from None
        except OverflowError:
            raise HTTPException(429, "sandbox busy; retry after the active job finishes") from None
        except (ValueError, TypeError):
            raise HTTPException(400, "invalid job") from None
        except Exception:
            raise HTTPException(503, "isolated runtime unavailable") from None

    @app.post("/admin/secrets/{action}")
    async def secrets(action: str, request: Request, background: BackgroundTasks):
        platform_key = os.environ.get("PLATFORM_CONTROL_KEY", "")
        if len(platform_key) < 32 or not hmac.compare_digest(
            request.headers.get("x-platform-control-key", ""), platform_key
        ):
            raise HTTPException(401, "unauthorized")
        store = app.state.secrets
        if store is None:
            raise HTTPException(503, "secret store unavailable")
        raw = await request.body()
        if len(raw) > 32768:
            raise HTTPException(413, "request too large")
        if action == "metadata":
            return await asyncio.to_thread(store.metadata)
        if action != "set":
            raise HTTPException(404, "unknown action")
        try:
            payload = json.loads(raw)
            consumers = await asyncio.to_thread(
                store.update, payload.get("name"), payload.get("value")
            )
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(400, "invalid credential change") from None
        background.add_task(store.restart, consumers)
        return {
            "ok": True,
            "name": payload["name"],
            "rolled": consumers,
            "roll_errors": [],
            "in_effect": False,
            "note": "restart scheduled; verify service health before using the new key",
        }

    return app


if __name__ == "__main__":
    if os.environ.get("REAPER_ONCE") == "1":
        configured_controller().reap()
    else:
        import uvicorn

        uvicorn.run(create_app(), host="0.0.0.0", port=4600, access_log=False)

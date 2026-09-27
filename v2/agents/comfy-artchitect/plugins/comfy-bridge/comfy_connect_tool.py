"""`comfy_connect` — where THIS ACCOUNT's ComfyUI runs: a rented GPU, or the person's own.

NOTHING IS RENTED UNTIL THE PERSON CHOOSES. The window asks when the account has no choice yet
(and its Connection section changes it later); the agent calls this only on the person's own
words — a link they pasted, or "rent one". The choice is the account's, so every chat uses it
(AccountConnectionRepository); on their own machine nothing is rented, leased or billed.

ON A VAST MACHINE IT ALSO SETTLES WHERE MODELS LIVE: on a volume if one is attached, so they
outlive the machine (ModelStorageSetupClient), else its own disk. The answer is kept on the
record for the window to show; a check still running when connect returns is finished by the
next `status`.
"""

from __future__ import annotations

from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace
from agent_runtime.infrastructure.net.outbound import fetch

from account_connection_repository import RENTED, USER_VAST, AccountConnectionRepository
from comfy_connection_probe import ComfyConnectionProbe
from model_storage_detector import DISK, VOLUME, WORKSPACE_VOLUME
from model_storage_setup_client import PENDING, ModelStorageSetupClient
from vast_account_client import VastAccountClient
from vast_machine_connector import VastMachineConnector

#: How long connect waits for the storage check; a restart of ComfyUI can outlast it.
_STORAGE_WAIT_S = 60.0
_UNCHECKED = "unchecked"
#: What the user is told when nothing is chosen and a machine is needed — the same sentence
#: everywhere (gpu_ensure, the comfy tools), so it reads the same whoever says it.
_ASK = ('Tell the user plainly, in these words or close to them: "Before I can run this, choose where it runs: rent a GPU on our servers (it uses credits while it runs), or — if you have your own ComfyUI — connect it in Workspace → Connection." Then stop; do not rent anything yourself.')


def _view(record: dict | None) -> dict:
    """What the window shows. kind None = not chosen yet.

    `link` (the person's own link, token included) rides in `details` only — the window's, to
    keep it in the field — and never in the text the model reads."""
    if record is None:
        return {"kind": None}
    storage = record.get("storage") or {}
    return {
        "kind": record["kind"],
        "label": record.get("label") or "",
        "link": record.get("link") or "",
        "vast_machine_id": record.get("vast_machine_id"),
        "downloads": record["kind"] in (RENTED, USER_VAST),
        "storage": {"kind": storage["kind"], "path": storage.get("path") or "",
                    "error": storage.get("error") or ""} if storage else None,
    }


def _settle_storage(repository: AccountConnectionRepository, record: dict | None) -> dict | None:
    """A storage check left running by connect: read its answer if it is there now."""
    storage = (record or {}).get("storage") or {}
    if storage.get("kind") != PENDING:
        return record
    answer = ModelStorageSetupClient(fetch=fetch).read(record, storage["nonce"])
    if answer is None:
        return record
    record = {**record, "storage": answer}
    repository.save(record)
    return record


class ComfyConnectTool(Tool):
    name = "comfy_connect"
    label = "Choose where ComfyUI runs"
    default_retryable = False
    #: Checking the link, then the storage check on a Vast machine (which may restart ComfyUI and
    #: waits up to _STORAGE_WAIT_S) — more than the sandbox's 120 s default.
    default_timeout_sec = 240
    description = (
        "Where this user's ComfyUI runs — one choice for the whole account, every chat. Only on "
        "the user's own words: action 'connect' with a link they gave (their Vast machine's "
        "Instance Portal link from Vast's Open button works exactly like the rented GPU, "
        "installs included; any other ComfyUI address works, but models install there only "
        "through ComfyUI-Manager or by hand); action 'rent' when they say to rent a GPU from us "
        "(it costs credits while it runs); 'status' says which it is, or that they have not "
        "chosen yet. 'vast_machines' lists the machines on their Vast account (their Vast API "
        "key, saved in the Connection section) and 'use_vast_machine' with a machine_id connects "
        "one. Never choose for them and never ask for a link — the window asks."
    )
    parameters = {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {"type": "string",
                       "enum": ["connect", "rent", "status", "vast_machines", "use_vast_machine"]},
            "url": {"type": "string", "description": "The link the user gave, exactly as given."},
            "machine_id": {"type": "integer", "description": "A Vast machine id from 'vast_machines'."},
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        repository = AccountConnectionRepository(Path(current_workspace(".") or "."))
        action = str(params.get("action") or "")
        try:
            if action == "status":
                view = _view(_settle_storage(repository, repository.read()))
            elif action == "rent":
                repository.save({"kind": RENTED})
                view = _view(repository.read())
            elif action == "connect":
                view = _view(self._connect(repository, ComfyConnectionProbe(fetch).probe(str(params.get("url") or ""))))
            elif action == "vast_machines":
                machines = VastAccountClient(fetch).machines()
                return ToolResult.text(self._list(machines), details={"machines": machines})
            elif action == "use_vast_machine":
                connector = VastMachineConnector(VastAccountClient(fetch), ComfyConnectionProbe(fetch))
                view = _view(self._connect(repository, connector.connect(int(params.get("machine_id") or 0))))
            else:
                return ToolResult.text(
                    "action must be connect, rent, status, vast_machines or use_vast_machine", is_error=True
                )
            return ToolResult.text(self._say(view), details=view)
        except ValueError as e:
            return ToolResult.text(f"comfy_connect: {e}", is_error=True)

    @staticmethod
    def _connect(repository: AccountConnectionRepository, record: dict) -> dict:
        """Save a machine that answered; on a Vast machine, settle where its models live."""
        repository.save(record)
        if record["kind"] != USER_VAST:
            return record
        try:
            storage = ModelStorageSetupClient(fetch=fetch).run(record, _STORAGE_WAIT_S)
        except ValueError as e:
            # The machine is connected and works; only the storage answer is missing. Said, not
            # hidden — the downloader checks again before its first file.
            storage = {"kind": _UNCHECKED, "error": str(e)}
        record = {**record, "storage": storage}
        repository.save(record)
        return record

    @staticmethod
    def _list(machines: list[dict]) -> str:
        if not machines:
            return "No machines on the user's Vast account."
        return "The user's Vast machines:\n" + "\n".join(
            f"  {m['id']}: {m['name']} — {m['gpu']} — {m['status']}" for m in machines
        )

    @staticmethod
    def _say(view: dict) -> str:
        if view["kind"] is None:
            return ("The user has not chosen where ComfyUI runs yet. Keep designing; when a "
                    "machine is needed: " + _ASK)
        if view["kind"] == RENTED:
            return "This account rents a GPU from the platform (gpu_ensure starts it; credits while it runs)."
        if view["downloads"]:
            return (
                f"This account uses the user's own Vast machine ({view['label']}) in every chat. "
                "Every comfy tool talks to it, installs included; nothing is rented or billed for "
                "it, and gpu_ensure is not needed. " + ComfyConnectTool._storage_line(view["storage"])
            )
        return (
            f"This account uses the user's own ComfyUI at {view['label']} in every chat. Every "
            "comfy tool talks to it; nothing is rented or billed. Models install there only "
            "through ComfyUI-Manager — anything else, tell the user which file goes where."
        )

    @staticmethod
    def _storage_line(storage: dict | None) -> str:
        kind = (storage or {}).get("kind")
        if kind == VOLUME:
            return f"Models are saved to its volume at {storage['path']}, so they are kept when the machine goes."
        if kind == WORKSPACE_VOLUME:
            return "Its /workspace is a volume, so models are kept when the machine goes."
        if kind == _UNCHECKED:
            return (f"Its storage could not be checked ({storage['error']}); the downloader checks "
                    "again before the first model.")
        if kind == DISK:
            return "It has no volume: models are saved on its own disk and are lost if the machine is destroyed."
        return "Where its models are saved is still being checked."

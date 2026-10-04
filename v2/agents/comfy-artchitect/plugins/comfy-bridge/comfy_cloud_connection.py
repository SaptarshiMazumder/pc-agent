"""ComfyCloudConnection — where every ComfyUI call goes: Comfy Cloud, with the person's Comfy API key.

ONE PLACE, NOTHING TO CHOOSE. Comfy Cloud is serverless: nothing is rented, started, leased or
stopped — a job is billed for the GPU seconds it runs, to the person's Comfy plan. Its API is
ComfyUI's own (`/api/prompt`, `/api/object_info`, `/api/upload/image`, `/api/view`, …), so the
tools talk to it exactly as they talked to a box. Nothing checks the connection in advance: a
wrong or missing key fails the first call that needs it (validate, a run), in Comfy Cloud's words.

THE KEY IS A NAME. `${USER_COMFY_API_KEY}` (the person's key from platform.comfy.org, saved in
Settings) is substituted by the host as the request leaves, so this plugin never holds it; the
host drops it on a redirect to another host (/api/view answers with a signed storage URL).
"""

from __future__ import annotations


class ComfyCloudConnection:
    URL = "https://cloud.comfy.org"
    KIND = "comfy_cloud"
    #: The person's key, as a name the host fills in.
    KEY_REF = "${USER_COMFY_API_KEY}"

    @classmethod
    def record(cls) -> dict:
        return {"kind": cls.KIND, "url": cls.URL}

    @classmethod
    def headers(cls) -> dict:
        return {"X-API-Key": cls.KEY_REF}

    @classmethod
    def url(cls, path: str) -> str:
        return f"{cls.URL}{path}"


__all__ = ["ComfyCloudConnection"]

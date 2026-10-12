"""Serve existing Lambda-shaped handlers as bounded HTTP APIs on a VPS."""

from __future__ import annotations

import asyncio
import base64
import importlib
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response


def create_app(module=None):
    module = module or importlib.import_module("handler")
    if hasattr(module, "services"):
        module.services()  # Publishing must validate storage/key configuration before health is green.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    limit = int(os.environ.get("HTTP_MAX_BODY_BYTES", "1048576"))
    if limit < 1:
        raise ValueError("HTTP_MAX_BODY_BYTES must be positive")

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
    async def dispatch(request: Request, path: str):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > limit:
                return JSONResponse({"message": "request body too large"}, status_code=413)
        event = {
            "path": "/" + path,
            "httpMethod": request.method,
            "headers": dict(request.headers),
            "isBase64Encoded": True,
            "body": base64.b64encode(body).decode("ascii"),
        }
        try:
            result = await asyncio.to_thread(module.handler, event, None)
        except Exception:
            # Do not expose provider credentials/presigned URLs in traceback responses.
            return JSONResponse({"message": "service request failed"}, status_code=500)
        return Response(
            result.get("body", ""),
            status_code=result.get("statusCode", 500),
            headers=result.get("headers", {"Content-Type": "application/json"}),
        )

    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        create_app(),
        host="0.0.0.0",
        port=int(os.environ["PORT"]),
        access_log=False,
        timeout_keep_alive=10,
    )

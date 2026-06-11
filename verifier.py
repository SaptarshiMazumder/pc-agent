"""
Layer 2: verification. After the executor finishes a step, a separate model
call looks at the CURRENT screenshot and the step's done_when condition and
returns a strict pass/fail. This is what lets the orchestrator retry instead of
blindly trusting that the action worked.

Uses a vision-capable model (it has to read the screen) and structured JSON.
"""
import json
import os

from google.genai import types
from google.genai.types import Part

from gemini_agent import client

VERIFIER_MODEL = os.getenv("VERIFIER_MODEL", "gemini-2.5-flash")

_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "ok": types.Schema(type=types.Type.BOOLEAN),
        "reason": types.Schema(type=types.Type.STRING),
    },
    required=["ok", "reason"],
)


def verify(step: str, done_when: str, screenshot_png: bytes) -> tuple[bool, str]:
    resp = client.models.generate_content(
        model=VERIFIER_MODEL,
        contents=[
            "Judge ONLY from the screenshot whether the step succeeded. "
            f"Step attempted: {step}\n"
            f"Success means: {done_when}\n"
            "Be strict: set ok=true only if the screen clearly shows the success "
            "condition is met. Give a one-sentence reason.",
            Part.from_bytes(data=screenshot_png, mime_type="image/png"),
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=_SCHEMA),
    )
    d = json.loads(resp.text)
    return bool(d.get("ok")), d.get("reason", "")

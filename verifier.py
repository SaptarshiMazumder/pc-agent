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

from gemini_agent import generate

VERIFIER_MODEL = os.getenv("VERIFIER_MODEL", "gemini-2.5-flash")

_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "ok": types.Schema(type=types.Type.BOOLEAN),
        "reason": types.Schema(type=types.Type.STRING),
    },
    required=["ok", "reason"],
)


def verify_text(step: str, done_when: str, evidence: str) -> tuple[bool, str]:
    """Text-based verification for the tool agent: judge from the agent's output
    and tool results (no screenshot)."""
    resp = generate(
        VERIFIER_MODEL,
        "Decide whether the step achieved its INTENT, based on the evidence "
        "(the agent's actions, command output, and final text).\n"
        f"Step attempted: {step}\n"
        f"Intended outcome (done_when): {done_when}\n"
        "Judge the SUBSTANCE, not the format. If the agent obtained the needed "
        "information or made the intended change, set ok=true even when the "
        "wording, format, or exact value differs from the literal done_when — "
        "e.g. 'there are 16 .py files' fully satisfies 'output a count'. Set "
        "ok=false only if the underlying goal clearly was NOT achieved (an error, "
        "a wrong result, or no evidence it happened). One-sentence reason.\n\n"
        f"--- evidence ---\n{evidence}",
        types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=_SCHEMA),
    )
    d = json.loads(resp.text)
    return bool(d.get("ok")), d.get("reason", "")


def verify(step: str, done_when: str, screenshot_png: bytes) -> tuple[bool, str]:
    resp = generate(
        VERIFIER_MODEL,
        [
            "Judge from the screenshot whether the step achieved its INTENT.\n"
            f"Step attempted: {step}\n"
            f"Intended outcome (done_when): {done_when}\n"
            "Judge the substance, not the exact wording: set ok=true if the screen "
            "shows the intended state was reached, even if details differ from the "
            "literal description. Set ok=false only if it clearly was NOT achieved "
            "(wrong page, an error, or no evidence). One-sentence reason.",
            Part.from_bytes(data=screenshot_png, mime_type="image/png"),
        ],
        types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=_SCHEMA),
    )
    d = json.loads(resp.text)
    return bool(d.get("ok")), d.get("reason", "")

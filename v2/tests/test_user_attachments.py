"""User-supplied attachments (the inbound counterpart to tool-declared artifacts).

A user turn may carry files the user handed in — e.g. an edited image sent from the
desktop canvas. They are saved to the workspace and carried BY REFERENCE on the
UserMessage (lean transcript), rendered by the client like any artifact, and any IMAGE is
inlined into the LLM request so a vision model can SEE it.
"""

import base64
from pathlib import Path

from agentd.domain.messages import (
    Artifact,
    UserMessage,
    message_from_dict,
    message_to_dict,
)
from agentd.infrastructure.files import image_data_url, save_upload
from agentd.infrastructure.llm.litellm import messages_to_litellm

_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32
_PNG_B64 = base64.b64encode(_PNG_BYTES).decode()


# ---- files.py: save an upload -> typed artifact under the workspace --------------


def test_save_upload_writes_and_classifies(tmp_path):
    info = save_upload(tmp_path / "uploads", "chart.png", _PNG_B64)
    stored = Path(info["path"])
    assert stored.exists() and stored.read_bytes() == _PNG_BYTES
    assert info["kind"] == "image" and info["mime"] == "image/png"
    assert info["name"] == "chart.png" and info["size"] == len(_PNG_BYTES)
    assert stored.parent.name == "uploads"


def test_save_upload_sanitizes_name_and_avoids_collisions(tmp_path):
    a = save_upload(tmp_path, "../../etc/pass wd.png", _PNG_B64)
    b = save_upload(tmp_path, "../../etc/pass wd.png", _PNG_B64)
    # no path traversal — the stored file stays inside dest
    assert Path(a["path"]).parent == tmp_path
    # two uploads of the same name never clobber each other
    assert a["path"] != b["path"]


def test_save_upload_rejects_bad_base64(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        save_upload(tmp_path, "x.png", "not@@base64!!")


def test_image_data_url_only_for_images(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(_PNG_BYTES)
    url = image_data_url(p)
    assert url and url.startswith("data:image/png;base64,")
    doc = tmp_path / "a.txt"
    doc.write_text("hi")
    assert image_data_url(doc) is None  # not an image
    assert image_data_url(tmp_path / "missing.png") is None


# ---- domain: attachments persist/transport with the user message -----------------


def test_user_message_roundtrip_carries_attachments(tmp_path):
    att = Artifact(
        path=str(tmp_path / "edit.png"), name="edit.png", mime="image/png", kind="image", size=40
    )
    m = UserMessage(content="make the collar label bigger", attachments=[att])
    d = message_to_dict(m)
    assert d["attachments"][0]["name"] == "edit.png"
    back = message_from_dict(d)
    assert isinstance(back.attachments[0], Artifact) and back.attachments[0].kind == "image"


def test_plain_user_message_has_no_attachments_key():
    # a text-only turn stays byte-identical to old records (no attachments key)
    assert "attachments" not in message_to_dict(UserMessage(content="hi"))
    assert message_from_dict({"role": "user", "content": "hi"}).attachments == []


# ---- adapter: an attached image is inlined for a vision model --------------------


def test_messages_to_litellm_inlines_image_attachment(tmp_path):
    img = tmp_path / "edit.png"
    img.write_bytes(_PNG_BYTES)
    att = Artifact(path=str(img), name="edit.png", mime="image/png", kind="image", size=40)
    msgs = [UserMessage(content="see this change", attachments=[att])]
    out = messages_to_litellm("sys", msgs)
    user = out[-1]
    assert user["role"] == "user" and isinstance(user["content"], list)
    kinds = [part["type"] for part in user["content"]]
    assert "text" in kinds and "image_url" in kinds
    img_part = next(p for p in user["content"] if p["type"] == "image_url")
    assert img_part["image_url"]["url"].startswith("data:image/png;base64,")


def test_messages_to_litellm_mentions_non_image_attachment(tmp_path):
    doc = tmp_path / "notes.pdf"
    doc.write_bytes(b"%PDF-1.4 stub")
    att = Artifact(path=str(doc), name="notes.pdf", mime="application/pdf", kind="file", size=12)
    out = messages_to_litellm("sys", [UserMessage(content="review", attachments=[att])])
    text = " ".join(p["text"] for p in out[-1]["content"] if p["type"] == "text")
    assert "notes.pdf" in text  # non-image files are mentioned, not inlined


def test_messages_to_litellm_plain_text_stays_string():
    out = messages_to_litellm("sys", [UserMessage(content="hello")])
    assert out[-1] == {"role": "user", "content": "hello"}


# ---- routing: a user image attachment must escalate to the vision brain ----------


def test_router_escalates_on_user_image_attachment():
    from agentd.infrastructure.llm.model_router import CostEfficiencyRouter

    r = CostEfficiencyRouter(text_model="deepseek/text", vision_model="gemini/vision")
    img = Artifact(path="x.png", name="x.png", mime="image/png", kind="image", size=1)
    doc = Artifact(path="x.pdf", name="x.pdf", mime="application/pdf", kind="file", size=1)
    assert r("base", [UserMessage(content="hi")]) == "deepseek/text"  # text-only
    assert r("base", [UserMessage(content="see", attachments=[img])]) == "gemini/vision"  # image!
    assert (
        r("base", [UserMessage(content="see", attachments=[doc])]) == "deepseek/text"
    )  # non-image


def test_has_image_ignores_tool_declared_artifacts():
    # a tool's DECLARED image deliverable is presentation-only (never sent to the model), so it
    # must NOT trigger the (expensive) vision brain
    from agentd.domain.messages import ToolResultMessage
    from agentd.infrastructure.llm.model_router import _has_image

    art = Artifact(path="out.png", name="out.png", mime="image/png", kind="image", size=1)
    tr = ToolResultMessage(tool_call_id="1", tool_name="render", artifacts=[art])
    assert _has_image([tr]) is False

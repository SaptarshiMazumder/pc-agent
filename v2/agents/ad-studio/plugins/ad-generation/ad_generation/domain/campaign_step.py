"""One step of a campaign's checklist: a reusable action, with what it was last run with and the
output the user chose to carry forward.

    brief    write (or rewrite) the brief: the look and each scene's prompts
    sheet    the shoot sheet: the cast member in the ad's outfit and light
    product_sheet  the product alone, six views in a fixed grid; its panels become references
    images   images from a prompt and references (a scene of the brief, or the step's own prompt)
    video    a clip from a first frame (by default the pick of its `source` step)

A step can be run any number of times; every run ADDS outputs, nothing is replaced. `pick` is the
output the user chose; it is what later steps build on (a video step's first frame is its source
step's pick). Nothing about a step locks it: a step already done is run again the same way.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

ACTIONS = ("brief", "sheet", "product_sheet", "images", "video")
STATUSES = ("todo", "done", "skipped")
TEXT_MODES = ("", "overlay")  # a text ad's words: "" drawn by the image model, "overlay" set by code


@dataclass(frozen=True)
class CampaignStep:
    id: str
    title: str
    action: str
    status: str = "todo"
    # Where its default prompt comes from: a scene of the brief ("s1"). "" = it has its own prompt.
    scene: str = ""
    # video: the step whose pick is the first frame.
    source: str = ""
    # Its own prompt and references; empty = the defaults (the brief's scene; the cast and
    # product references). Set by the user or the agent for a step of their own.
    prompt: str = ""
    references: tuple[str, ...] = ()
    cast: bool = True  # a person appears: the cast member's sheet rides along and is checked
    shows_product: bool = True  # the product appears: its photos ride along and are checked
    count: int = 0  # images per run; 0 = the recipe's
    pick: str = ""  # the output the user chose
    # What it was last run with, so the next run starts from the same settings.
    model: str = ""  # "provider/model"
    seconds: int = 0
    resolution: str = ""
    # A run the agent proposed and the user has not approved yet: {"tool": ..., "args": {...}}.
    # Shown pre-filled in the studio; the user picks the model and generates, or dismisses it.
    proposal: dict = field(default_factory=dict)
    # A text ad's design step: "overlay" = the picture without text, the words set on it in real
    # fonts as editable layers; "" = the image model draws the words.
    text: str = ""

    def __post_init__(self) -> None:
        if self.action not in ACTIONS:
            raise ValueError(f"step {self.id}: action '{self.action}' is not one of {', '.join(ACTIONS)}")
        if self.status not in STATUSES:
            raise ValueError(f"step {self.id}: status '{self.status}' is not one of {', '.join(STATUSES)}")
        if self.text not in TEXT_MODES or (self.text and self.action != "images"):
            raise ValueError(f"step {self.id}: text is '' or 'overlay' (images steps only), not '{self.text}'")
        if self.action == "video" and not self.source:
            raise ValueError(f"step {self.id}: a video step names the image step it starts from (source)")

    def with_(self, **changes) -> "CampaignStep":
        return replace(self, **changes)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "action": self.action,
            "status": self.status,
            "scene": self.scene,
            "source": self.source,
            "prompt": self.prompt,
            "references": list(self.references),
            "cast": self.cast,
            "shows_product": self.shows_product,
            "count": self.count,
            "pick": self.pick,
            "model": self.model,
            "seconds": self.seconds,
            "resolution": self.resolution,
            "proposal": dict(self.proposal),
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CampaignStep":
        return cls(
            id=str(data["id"]).strip(),
            title=str(data.get("title") or data["id"]).strip(),
            action=str(data["action"]),
            status=str(data.get("status") or "todo"),
            scene=str(data.get("scene") or ""),
            source=str(data.get("source") or ""),
            prompt=str(data.get("prompt") or ""),
            references=tuple(str(r) for r in data.get("references") or ()),
            cast=bool(data.get("cast", True)),
            shows_product=bool(data.get("shows_product", True)),
            count=int(data.get("count") or 0),
            pick=str(data.get("pick") or ""),
            model=str(data.get("model") or ""),
            seconds=int(data.get("seconds") or 0),
            resolution=str(data.get("resolution") or ""),
            proposal=dict(data.get("proposal") or {}),
            text=str(data.get("text") or ""),
        )

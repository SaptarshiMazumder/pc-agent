"""One image or clip a provider produced, with what it cost — the unit of the cost ledger."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class GeneratedMedia:
    path: str  # workspace path of the downloaded file
    kind: str  # "image" | "video"
    provider: str
    model: str
    cost_usd: float
    # "exact" when the provider reported usage we priced; "estimate" when priced from the table.
    cost_basis: str
    # A still of the clip's last frame, when the provider returns one — what a clip is checked by.
    last_frame: str = ""
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

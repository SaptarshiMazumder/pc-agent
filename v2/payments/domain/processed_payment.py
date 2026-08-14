"""ProcessedPayment — what the other side did once the money was real.

DELIBERATELY OPAQUE. `detail` is a dict this module never reads. Processing a payment means
posting a ledger transaction, adding credits and extending access — none of which are payment
concepts, and all of which the caller needs back in its HTTP response. Carrying them through as
an untyped bag is what lets the rail stay ignorant of the shop.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProcessedPayment:
    #: The processor's own id for what it did (the ledger transaction id), for correlation.
    reference: str
    #: False when this exact payment had already been processed. The caller MUST distinguish it:
    #: reporting a replay as new revenue is how a retried request becomes a phantom sale.
    created: bool
    detail: dict = field(default_factory=dict)

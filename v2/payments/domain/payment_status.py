"""The states a payment can be in, and which of them are final.

THE ONE THAT MATTERS IS `REQUIRES_ACTION`. It is the state the old `Charge(ok=True|False)` shape
could not express, and the reason a real card rail could never be dropped into this system: with
3-D Secure — mandatory in India and the EU — the customer leaves the page to authenticate, and
the answer arrives later, over a webhook, on a different request. A boolean has nowhere to put
"ask the human and come back".
"""

from __future__ import annotations

PENDING = "pending"
REQUIRES_ACTION = "requires_action"
SUCCEEDED = "succeeded"
FAILED = "failed"
REFUNDED = "refunded"

#: No further event will change these. Anything else may still move.
TERMINAL = frozenset({SUCCEEDED, FAILED, REFUNDED})

ALL = frozenset({PENDING, REQUIRES_ACTION, SUCCEEDED, FAILED, REFUNDED})


def require_known(status: str) -> str:
    """Reject a status this system has no rule for.

    An unrecognised status must never fall through to "not succeeded and therefore fine": that is
    how a paid customer silently receives nothing. A rail growing a new state is a thing we have
    to be told about.
    """
    if status not in ALL:
        raise ValueError(f"unknown payment status {status!r}; expected one of {sorted(ALL)}")
    return status

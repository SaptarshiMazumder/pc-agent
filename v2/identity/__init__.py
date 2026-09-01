"""Identity — who a caller is, and how they prove it.

THE SPLIT THIS MODULE EXISTS TO DRAW. ``v2/accounts`` owns what an account HAS: budgets, credit
grants, the spend ledger, entitlements. This module owns who someone IS: credentials, tokens,
signing keys, and the link between an external login and an account id.

The line is drawn exactly there because identity is the half we may one day outsource (Cognito,
Google, Microsoft all do it) and the money half is the one nobody else can ever run for us. With
both in one file — as they were, PBKDF2 hashing and the double-entry ledger in ``accounts/app.py``
— swapping the identity provider means operating on the file that holds the money.

THE CONTRACT BETWEEN THE TWO IS ONE STRING: ``account_id``. Identity's output is a token whose
``sub`` is an ``acct_`` id; accounts' input is an ``acct_`` id. There is no other coupling, which
is why on the hot path the two never touch each other: the model proxy verifies a signature
locally (identity is not in the request at all) and then asks accounts about an id (no token is
involved).

Identity never imports ``ledger``. Accounts never imports a token issuer. Where identity must
create an account row for a first-time external login, it calls the ``AccountDirectory`` PORT and
accounts supplies the adapter — the same dependency inversion ``payments`` already uses for its
post-processor.

Layout mirrors ``v2/payments``: domain (no I/O), application (interfaces + orchestration),
infrastructure (adapters), main (the composition root), presentation (the FastAPI router).
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"

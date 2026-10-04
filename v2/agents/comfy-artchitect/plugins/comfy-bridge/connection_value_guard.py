"""ConnectionValueGuard — a value a PERSON or a MACHINE supplied never carries a secret's name.

The host fills every `${NAME}` this plugin declared (HF_TOKEN, the platform's service key, …) into
the URL, headers and body of a request at the last moment. That is how a key reaches a service
without the plugin ever holding it — and it is also why a link the person pastes, or a token or
address a Vast machine reports, must never contain `${`: `https://their-server/${HF_TOKEN}` would
come back filled in and be sent to their server. So such values are refused where they arrive, and
again whenever a saved connection is read back.
"""

from __future__ import annotations


class ConnectionValueGuard:
    MARK = "${"

    @classmethod
    def check(cls, what: str, *values: object) -> None:
        """Raise ValueError when any of `values` contains a placeholder."""
        for value in values:
            if isinstance(value, str) and cls.MARK in value:
                raise ValueError(f"{what} contains '{cls.MARK}' — that is not accepted in an address, link "
                                 "or token. Paste the link exactly as the provider gives it.")

    @classmethod
    def check_record(cls, what: str, record: dict) -> None:
        """Every string in a saved connection record."""
        cls.check(what, *[v for v in (record or {}).values() if isinstance(v, str)])


__all__ = ["ConnectionValueGuard"]

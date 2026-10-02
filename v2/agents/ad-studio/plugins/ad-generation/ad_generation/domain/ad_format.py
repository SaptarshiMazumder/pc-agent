"""A proven ad format — the playbook a brief is written from. Data, not code: one file each."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdFormat:
    key: str  # the file name, e.g. "street-walk"
    title: str  # the file's first heading
    guide: str  # the whole playbook, handed to the brief writer as written

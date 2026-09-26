"""ProofRunner — checks one contract criterion itself, rather than taking the developer's word."""

from __future__ import annotations

from typing import Protocol

from agent_runtime.domain.deliverable_contract import Criterion
from agent_runtime.domain.proof_result import ProofResult


class ProofRunner(Protocol):
    async def prove(self, criterion: Criterion) -> ProofResult: ...


__all__ = ["ProofRunner"]

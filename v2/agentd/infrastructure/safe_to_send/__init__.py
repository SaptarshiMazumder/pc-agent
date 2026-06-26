"""Egress privacy gate — an independent "is this SAFE TO SEND?" check on channel replies."""

from agentd.infrastructure.safe_to_send.factory import build_safe_to_send_gate
from agentd.infrastructure.safe_to_send.gate import DEFAULT_SAFE_REPLY, LlmSafeToSendGate

__all__ = ["build_safe_to_send_gate", "LlmSafeToSendGate", "DEFAULT_SAFE_REPLY"]

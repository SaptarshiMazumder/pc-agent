"""Marketplace infrastructure (M4): the concrete registry client, bundle IO,
installer, ledger, and the factory the composition roots (gateway + CLI) use."""

from agent_runtime.infrastructure.marketplace.factory import build_marketplace_service

__all__ = ["build_marketplace_service"]

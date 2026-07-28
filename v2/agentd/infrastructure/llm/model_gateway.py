"""Deprecated compatibility import for the renamed Model Proxy client seam.

New code imports :mod:`agentd.infrastructure.llm.model_proxy`. This module remains so
older plugins and integrations do not break during the naming migration.
"""

from agentd.infrastructure.llm.model_proxy import apply, configure, enabled, status

__all__ = ["apply", "configure", "enabled", "status"]

"""agentd - minimal agent gateway daemon."""

__version__ = "0.1.9"

# BEFORE ANY THIRD-PARTY IMPORT. litellm fixes its tiktoken cache to a directory inside its own
# site-packages folder the moment it is imported, and on an install the user cannot write to
# (C:\Program Files, an enterprise HKLM deployment) the first token count then fails with EACCES.
# Redirecting it here — the one module every entry point loads first — is what makes the runtime
# work from a read-only install directory at all. See runtime_paths.redirect_library_caches.
from agent_runtime import runtime_paths as _runtime_paths  # noqa: E402

_runtime_paths.redirect_library_caches()

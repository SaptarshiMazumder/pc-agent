"""Encrypted credential vault: per-(agent, site) logins, Fernet-encrypted at rest, unlocked by
the AGENTD_VAULT_KEY master key. Used by the simple_login tool; passwords never reach the model."""

from agentd.infrastructure.credentials.connect import ConnectTokenStore
from agentd.infrastructure.credentials.file_store import (
    EncryptedFileCredentialStore,
    build_credential_store,
)

__all__ = ["EncryptedFileCredentialStore", "build_credential_store", "ConnectTokenStore"]

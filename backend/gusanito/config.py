import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    # Local (Azurite): a connection string. On Azure: the account name + managed identity.
    storage_connection_string: str | None
    storage_account: str | None
    claude_model: str
    claude_effort: str
    # Illustrative cut-off: calibrate it with agronomist-labelled photos.
    min_confidence: float
    report_token: str | None


def load() -> Config:
    connection = os.environ.get("DATA_STORAGE_CONNECTION_STRING") or None
    account = os.environ.get("DATA_STORAGE_ACCOUNT") or None
    if not connection and not account:
        raise ConfigError("set DATA_STORAGE_CONNECTION_STRING or DATA_STORAGE_ACCOUNT")
    return Config(
        storage_connection_string=connection,
        storage_account=account,
        claude_model=os.environ.get("CLAUDE_MODEL", "claude-opus-5-5"),
        claude_effort=os.environ.get("CLAUDE_EFFORT", "medium"),
        min_confidence=float(os.environ.get("MIN_CONFIDENCE", "0.7")),
        report_token=os.environ.get("REPORT_TOKEN") or None,
    )


def require_anthropic_key() -> None:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    # An unresolved Key Vault reference reaches the app as the literal "@Microsoft.KeyVault(...)".
    if not key or key.startswith("@Microsoft.KeyVault"):
        raise ConfigError("ANTHROPIC_API_KEY is not set or its Key Vault reference did not resolve")

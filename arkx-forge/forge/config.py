"""Configuration. All secrets come from the environment, never from disk or the browser."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "high"


def _home() -> Path:
    return Path(os.environ.get("FORGE_HOME", Path.home() / ".arkx-forge")).expanduser()


@dataclass(frozen=True)
class Settings:
    home: Path = field(default_factory=_home)
    host: str = os.environ.get("FORGE_HOST", "127.0.0.1")
    port: int = int(os.environ.get("FORGE_PORT", "8420"))
    model: str = os.environ.get("FORGE_MODEL", DEFAULT_MODEL)
    effort: str = os.environ.get("FORGE_EFFORT", DEFAULT_EFFORT)
    # "anthropic" (real API) or "mock" (offline deterministic double, for dev/tests)
    provider: str = os.environ.get("FORGE_PROVIDER", "anthropic")
    max_tokens: int = int(os.environ.get("FORGE_MAX_TOKENS", "32000"))
    # Per-command wall clock ceiling, seconds.
    command_timeout: int = int(os.environ.get("FORGE_COMMAND_TIMEOUT", "300"))
    # How long a task waits for a human decision on a guarded command.
    confirm_timeout: int = int(os.environ.get("FORGE_CONFIRM_TIMEOUT", "600"))
    # Max tool round-trips per agent stage, a runaway guard.
    max_iterations: int = int(os.environ.get("FORGE_MAX_ITERATIONS", "40"))
    max_read_bytes: int = int(os.environ.get("FORGE_MAX_READ_BYTES", "200000"))

    @property
    def db_path(self) -> Path:
        return self.home / "forge.db"

    @property
    def api_key(self) -> str | None:
        """Server-side only. Never serialised into an API response or the frontend."""
        return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")

    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def effective_model(self) -> str:
        """What is actually answering. The mock provider must never masquerade
        as a Claude model in the UI or the CLI."""
        if self.provider == "mock":
            return "mock-provider (no Claude API call)"
        if ":" in self.provider:
            return f"{self.provider} (custom provider)"
        return self.model

    def public_dict(self) -> dict:
        """Safe to hand to the browser: no credentials."""
        return {
            "model": self.effective_model,
            "configured_model": self.model,
            "effort": self.effort,
            "provider": self.provider,
            "credentials_present": bool(self.api_key) or self.provider == "mock",
            "command_timeout": self.command_timeout,
            "home": str(self.home),
        }


SETTINGS = Settings()
SETTINGS.home.mkdir(parents=True, exist_ok=True)

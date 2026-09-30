"""Provider selection. The one place that knows which backends exist."""
from __future__ import annotations

from ..config import Settings


def build_provider(settings: Settings):
    """Factory for the model backend.

    `FORGE_PROVIDER` accepts:
      anthropic          the Claude API (default)
      mock               the offline double above
      module.path:Name   any class taking Settings and implementing llm.base.Provider,
                         which is how another vendor or a local model is added without
                         touching the agent layer.
    """
    spec = settings.provider
    if spec == "mock":
        from .mock import MockProvider
        return MockProvider(settings)
    if spec == "anthropic":
        from .anthropic_client import AnthropicProvider
        return AnthropicProvider(settings)
    if ":" in spec:
        import importlib
        module_name, _, attr = spec.partition(":")
        try:
            cls = getattr(importlib.import_module(module_name), attr)
        except (ImportError, AttributeError) as exc:
            raise ValueError(f"cannot load provider {spec!r}: {exc}") from exc
        provider = cls(settings)
        for required in ("turn", "model", "name"):
            if not hasattr(provider, required):
                raise ValueError(f"provider {spec!r} is missing `{required}`")
        return provider
    raise ValueError(f"unknown provider: {spec!r} "
                     f"(use 'anthropic', 'mock', or 'module.path:ClassName')")

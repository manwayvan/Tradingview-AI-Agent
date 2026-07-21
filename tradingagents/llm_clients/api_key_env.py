"""Canonical provider -> API-key env-var mapping.

A single source of truth for which environment variable holds the API
key for each supported LLM provider. Used by the CLI's interactive key
prompt (cli/utils.ensure_api_key) and by anything else that needs to
ask "does this provider require a key, and which env var is it?".

When adding a new provider, register its env var here so the CLI flow
prompts for it automatically instead of failing on first API call.
"""

from __future__ import annotations

import os

PROVIDER_API_KEY_ENV: dict[str, str | None] = {
    "openai":     "OPENAI_API_KEY",
    "anthropic":  "ANTHROPIC_API_KEY",
    "google":     "GOOGLE_API_KEY",
    "azure":      "AZURE_OPENAI_API_KEY",
    # Bedrock authenticates via the AWS credential chain, not a single key env.
    "bedrock":    None,
    "xai":        "XAI_API_KEY",
    "deepseek":   "DEEPSEEK_API_KEY",
    # Dual-region providers each carry their own account; keys are not
    # interchangeable between the international and China endpoints.
    "qwen":       "DASHSCOPE_API_KEY",
    "qwen-cn":    "DASHSCOPE_CN_API_KEY",
    "glm":        "ZHIPU_API_KEY",
    "glm-cn":     "ZHIPU_CN_API_KEY",
    "minimax":    "MINIMAX_API_KEY",
    "minimax-cn": "MINIMAX_CN_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    # Additional hosted OpenAI-compatible providers (model is user-specified).
    # kimi -> Moonshot AI; nvidia -> NVIDIA NIM.
    "mistral":    "MISTRAL_API_KEY",
    "kimi":       "MOONSHOT_API_KEY",
    "groq":       "GROQ_API_KEY",
    "nvidia":     "NVIDIA_API_KEY",
    # Local runtimes do not authenticate.
    "ollama":     None,
    # Generic OpenAI-compatible endpoint: the client reads this when set (keyed
    # relays), but it is marked key-optional in the provider registry so the CLI
    # never forces a prompt and keyless local servers still work.
    "openai_compatible": "OPENAI_COMPATIBLE_API_KEY",
}

# Hosted providers probed (in order) when the configured provider has no key.
# Keep this to services that ship catalog defaults so auto-switch is safe.
_AUTODETECT_ORDER: tuple[str, ...] = (
    "openai",
    "anthropic",
    "google",
    "xai",
    "deepseek",
    "openrouter",
    "groq",
    "mistral",
    "kimi",
    "nvidia",
    "qwen",
    "qwen-cn",
    "glm",
    "glm-cn",
    "minimax",
    "minimax-cn",
)


def get_api_key_env(provider: str) -> str | None:
    """Return the env var name for `provider`'s API key, or None if not applicable.

    Unknown providers also return None — callers should treat that as
    "no key check possible" rather than as "no key required".
    """
    return PROVIDER_API_KEY_ENV.get(provider.lower())


def _provider_key_optional(provider: str) -> bool:
    """True when the provider can run without an API key (local / optional)."""
    name = provider.lower()
    if name == "ollama":
        return True
    if name == "openai_compatible":
        return True
    return False


def api_key_configured(provider: str) -> bool:
    """Return True when `provider` has usable credentials in the environment."""
    name = provider.lower()
    if name == "bedrock":
        return bool(
            os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
            or os.environ.get("AWS_ACCESS_KEY_ID")
            or os.environ.get("AWS_PROFILE")
        )
    if _provider_key_optional(name):
        return True
    env_var = get_api_key_env(name)
    if not env_var:
        return False
    return bool(os.environ.get(env_var))


def detect_llm_provider() -> str | None:
    """Return the first hosted provider that has an API key set, or None."""
    for provider in _AUTODETECT_ORDER:
        env_var = get_api_key_env(provider)
        if env_var and os.environ.get(env_var):
            return provider
    return None


def _default_models(provider: str) -> tuple[str, str]:
    """Return (deep, quick) catalog defaults for `provider`."""
    from tradingagents.llm_clients.model_catalog import get_model_options

    def _first(mode: str) -> str | None:
        try:
            options = get_model_options(provider, mode)
        except KeyError:
            return None
        for _label, model_id in options:
            if model_id and model_id != "custom":
                return model_id
        return None

    deep = _first("deep")
    quick = _first("quick")
    if not deep or not quick:
        raise ValueError(
            f"No catalog defaults for provider '{provider}' — set "
            "TRADINGAGENTS_DEEP_THINK_LLM / TRADINGAGENTS_QUICK_THINK_LLM."
        )
    return deep, quick


def resolve_llm_config(config: dict | None = None) -> dict:
    """Return a config copy with a provider that has credentials when possible.

    If the configured ``llm_provider`` has no API key but another hosted
    provider does, switch to that provider and its catalog default models.
    Does not invent keys — when nothing is configured the original provider
    is left in place so callers can surface a clear error.
    """
    from tradingagents.default_config import DEFAULT_CONFIG

    cfg = dict(config or DEFAULT_CONFIG)
    provider = str(cfg.get("llm_provider") or "openai").lower()
    cfg["llm_provider"] = provider

    if api_key_configured(provider):
        return cfg

    detected = detect_llm_provider()
    if not detected or detected == provider:
        return cfg

    deep, quick = _default_models(detected)
    cfg["llm_provider"] = detected
    cfg["deep_think_llm"] = deep
    cfg["quick_think_llm"] = quick
    # Drop a stale OpenAI/custom backend URL when switching providers.
    cfg["backend_url"] = None
    return cfg


def llm_status(config: dict | None = None) -> dict:
    """Public status snapshot for health checks and the Scanner UI."""
    cfg = resolve_llm_config(config)
    provider = str(cfg.get("llm_provider") or "openai").lower()
    env_var = get_api_key_env(provider)
    ready = api_key_configured(provider)
    if ready:
        message = f"LLM ready ({provider})"
    elif env_var:
        message = (
            f"API key for provider '{provider}' is not set. "
            f"Add {env_var} (or ANTHROPIC_API_KEY / GOOGLE_API_KEY) "
            "in Railway Variables, then redeploy."
        )
    else:
        message = f"Provider '{provider}' is not configured."
    return {
        "ready": ready,
        "provider": provider,
        "env_var": env_var,
        "deep_think_llm": cfg.get("deep_think_llm"),
        "quick_think_llm": cfg.get("quick_think_llm"),
        "message": message,
    }

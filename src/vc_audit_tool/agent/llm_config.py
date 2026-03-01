"""LLM provider configuration — loaded once at import time."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LLMProviderConfig:
    """Immutable configuration for a single LLM provider."""

    name: str  # e.g. "google", "openai", "anthropic", "ollama"
    env_key: str  # env var that gates availability
    model_default: str  # default model ID
    langchain_class: str  # dotted import path
    max_retries: int = 2
    cost_per_1k_input_usd: float = 0.0
    cost_per_1k_output_usd: float = 0.0


# ---------------------------------------------------------------------------
# Hardcoded defaults (used when YAML is missing or malformed)
# ---------------------------------------------------------------------------

_HARDCODED_DEFAULTS: list[LLMProviderConfig] = [
    LLMProviderConfig(
        name="google",
        env_key="GOOGLE_API_KEY",
        model_default="gemini-2.5-flash",
        langchain_class="langchain_google_genai.ChatGoogleGenerativeAI",
        max_retries=2,
        cost_per_1k_input_usd=0.000075,
        cost_per_1k_output_usd=0.0003,
    ),
    LLMProviderConfig(
        name="openai",
        env_key="OPENAI_API_KEY",
        model_default="gpt-4o-mini",
        langchain_class="langchain_openai.ChatOpenAI",
        max_retries=2,
        cost_per_1k_input_usd=0.00015,
        cost_per_1k_output_usd=0.0006,
    ),
    LLMProviderConfig(
        name="anthropic",
        env_key="ANTHROPIC_API_KEY",
        model_default="claude-3-5-haiku-20241022",
        langchain_class="langchain_anthropic.ChatAnthropic",
        max_retries=2,
        cost_per_1k_input_usd=0.0008,
        cost_per_1k_output_usd=0.004,
    ),
    LLMProviderConfig(
        name="ollama",
        env_key="OLLAMA_MODEL",
        model_default="llama3.2",
        langchain_class="langchain_ollama.ChatOllama",
        max_retries=2,
        cost_per_1k_input_usd=0.0,
        cost_per_1k_output_usd=0.0,
    ),
]

# ---------------------------------------------------------------------------
# YAML path resolution
# ---------------------------------------------------------------------------

# __file__ is  src/vc_audit_tool/agent/llm_config.py
# parents: [agent/, vc_audit_tool/, src/, repo_root/]
_REPO_ROOT = Path(__file__).parent.parent.parent.parent
_CONFIG_PATH = _REPO_ROOT / "config" / "llm_providers.yaml"


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _parse_provider(raw: dict) -> LLMProviderConfig:  # type: ignore[type-arg]
    """Convert a raw YAML dict to an LLMProviderConfig, validating required fields."""
    required = ("name", "env_key", "model_default", "langchain_class")
    for key in required:
        if key not in raw:
            raise ValueError(f"LLM provider entry missing required key: {key!r}")
    return LLMProviderConfig(
        name=str(raw["name"]),
        env_key=str(raw["env_key"]),
        model_default=str(raw["model_default"]),
        langchain_class=str(raw["langchain_class"]),
        max_retries=int(raw.get("max_retries", 2)),
        cost_per_1k_input_usd=float(raw.get("cost_per_1k_input_usd", 0.0)),
        cost_per_1k_output_usd=float(raw.get("cost_per_1k_output_usd", 0.0)),
    )


def load_llm_chain(
    config_path: Path | None = None,
) -> list[LLMProviderConfig]:
    """Load provider chain from config/llm_providers.yaml.

    Falls back to hardcoded defaults if:
    - The file does not exist
    - The file is malformed / contains invalid entries
    - The ``providers`` list is empty or missing
    """
    path = config_path if config_path is not None else _CONFIG_PATH

    if not path.exists():
        logger.debug("llm_providers.yaml not found at %s; using hardcoded defaults", path)
        return list(_HARDCODED_DEFAULTS)

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to parse llm_providers.yaml (%s); using hardcoded defaults", exc)
        return list(_HARDCODED_DEFAULTS)

    if not isinstance(data, dict) or "providers" not in data:
        logger.warning("llm_providers.yaml has no 'providers' key; using hardcoded defaults")
        return list(_HARDCODED_DEFAULTS)

    raw_providers = data["providers"]
    if not isinstance(raw_providers, list) or len(raw_providers) == 0:
        logger.warning("llm_providers.yaml 'providers' list is empty; using hardcoded defaults")
        return list(_HARDCODED_DEFAULTS)

    configs: list[LLMProviderConfig] = []
    for raw in raw_providers:
        try:
            configs.append(_parse_provider(raw))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping malformed provider entry %r: %s", raw, exc)

    if not configs:
        logger.warning("No valid providers in llm_providers.yaml; using hardcoded defaults")
        return list(_HARDCODED_DEFAULTS)

    return configs

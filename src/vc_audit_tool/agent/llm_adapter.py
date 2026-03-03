"""LLM provider adapter for the research agent.

Tries providers in priority order:
  Google Gemini Flash → OpenAI GPT-4o-mini → Anthropic Haiku → Ollama → None

Cost-aware patterns applied:
  1. Model routing   — lite model for small batches (≤15 snippets), full model for large
  2. Cost tracking   — immutable CostRecord / CostTracker; logged per call
  3. Narrow retry    — exponential backoff on transient errors, fast-fail on auth/bad-request
  4. Prompt caching  — system prompt sent with cache_control for Anthropic provider
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from vc_audit_tool.agent.cost_tracker import CostRecord, CostTracker, estimate_cost
from vc_audit_tool.agent.llm_config import LLMProviderConfig, load_llm_chain

logger = logging.getLogger(__name__)

# ── Optional LLM provider imports ────────────────────────────────────────

try:
    from langchain_core.messages import HumanMessage, SystemMessage
except ImportError:
    HumanMessage = None  # type: ignore[assignment,misc]
    SystemMessage = None  # type: ignore[assignment,misc]

try:
    from langchain_google_genai import ChatGoogleGenerativeAI
except ImportError:
    ChatGoogleGenerativeAI = None  # type: ignore[assignment,misc,unused-ignore]

try:
    from langchain_anthropic import ChatAnthropic
except ImportError:
    ChatAnthropic = None  # type: ignore[assignment,misc,unused-ignore]

try:
    from langchain_openai import ChatOpenAI
except ImportError:
    ChatOpenAI = None  # type: ignore[assignment,misc,unused-ignore]

try:
    from langchain_ollama import ChatOllama
except ImportError:
    ChatOllama = None  # type: ignore[assignment,misc,unused-ignore]

__all__ = [
    "HumanMessage",
    "SystemMessage",
    "_LLM_SYSTEM_PROMPT",
    "_get_llm",
    "_llm_extract_structured",
    "_extract_json_robust",
    "CostTracker",
]


# ── System prompt ────────────────────────────────────────────────────────

_LLM_SYSTEM_PROMPT = """\
You are a financial analyst. Read the web search snippets inside <snippets> tags \
and extract ONLY confirmed facts about the company named in <company>.

<task>
Return a single JSON object with exactly these keys. \
No markdown, no explanation — JSON only.
</task>

<fields>
- last_post_money_valuation: number or null
  <rule>POST-MONEY VALUATION = what the company is WORTH after the round closed.
  NOT the money raised. Example: "raised $1B at a $5B valuation" →
  last_post_money_valuation=5000000000, last_round_amount_raised=1000000000.
  If only a raise amount is disclosed and no valuation is mentioned, return null.</rule>

- last_round_date: string or null  (format: YYYY-MM-DD or "Month YYYY")

- last_round_amount_raised: number or null  (USD raised, NOT the valuation)

- revenue_ltm: number or null  (USD annual revenue or ARR)

- company_description: string or null  (1-2 sentences, business model + market)

- valuation_signals: array of objects
  <schema>[{"amount_usd": number, "source": string, "date": string|null,
             "type": "post_money"|"secondary_market"|"analyst_estimate"}]</schema>
</fields>

<rules>
- NEVER guess. Return null for any field you are not certain about.
- Distinguish between the round SIZE (money invested) and the POST-MONEY VALUATION.
- Use the MOST RECENT valuation you can confirm from the snippets.
</rules>\
"""

# ── Model routing thresholds ──────────────────────────────────────────────
# Small batches use the lite/cheaper model variant; large batches use full model.

_LITE_SNIPPET_THRESHOLD = 15  # snippets; below this → lite model
_LITE_MODEL_SUFFIX: dict[str, str] = {
    # Maps full model name → cheaper lite variant for small tasks.
    "gemini-2.5-flash": "gemini-2.0-flash-lite",
    "gpt-4o": "gpt-4o-mini",
}

# ── Retry config ─────────────────────────────────────────────────────────

_MAX_RETRIES = 3
_RETRY_BASE_SLEEP = 1.0  # seconds; doubles each attempt

# Exception class names that indicate transient failures worth retrying.
_TRANSIENT_ERROR_NAMES = frozenset(
    {
        "APIConnectionError",
        "InternalServerError",
        "RateLimitError",
        "ServiceUnavailable",
        "Timeout",
        "ConnectError",
        "ReadTimeout",
    }
)

# Exception class names that should fail immediately (no retry).
_FATAL_ERROR_NAMES = frozenset(
    {
        "AuthenticationError",
        "PermissionDeniedError",
        "BadRequestError",
        "InvalidRequestError",
        "NotFoundError",
    }
)


def _is_transient(exc: Exception) -> bool:
    return type(exc).__name__ in _TRANSIENT_ERROR_NAMES


def _is_fatal(exc: Exception) -> bool:
    return type(exc).__name__ in _FATAL_ERROR_NAMES


# ── Provider selection ───────────────────────────────────────────────────


def _select_model(
    provider_cfg: LLMProviderConfig,
    snippet_count: int,
) -> str:
    """Return the model ID to use, routing small tasks to a lite variant."""
    base_model = os.environ.get(
        f"{provider_cfg.name.upper()}_MODEL", provider_cfg.model_default
    )
    if snippet_count <= _LITE_SNIPPET_THRESHOLD:
        return _LITE_MODEL_SUFFIX.get(base_model, base_model)
    return base_model


def _get_llm(snippet_count: int = 999) -> tuple[Any, str, LLMProviderConfig | None]:
    """Return (llm_instance, model_label, provider_cfg) for the highest-priority
    available provider, routing to a lite model for small snippet batches.
    """
    for provider in load_llm_chain():
        if not os.environ.get(provider.env_key):
            continue

        model = _select_model(provider, snippet_count)
        try:
            if provider.name == "google" and ChatGoogleGenerativeAI is not None:
                llm: Any = ChatGoogleGenerativeAI(
                    model=model,
                    temperature=0,
                    max_output_tokens=2048,
                )
                return llm, f"google/{model}", provider
            if provider.name == "openai" and ChatOpenAI is not None:
                llm = ChatOpenAI(model=model, temperature=0)
                return llm, f"openai/{model}", provider
            if provider.name == "anthropic" and ChatAnthropic is not None:
                llm = ChatAnthropic(model=model, temperature=0, max_tokens=2048)
                return llm, f"anthropic/{model}", provider
            if provider.name == "ollama" and ChatOllama is not None:
                base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
                llm = ChatOllama(
                    model=model,
                    base_url=base_url,
                    temperature=0,
                    num_predict=512,
                )
                return llm, f"ollama/{model}", provider
        except Exception as exc:
            logger.warning("%s init failed (%s)", provider.name.capitalize(), exc)

    return None, "", None


# ── Structured extraction ────────────────────────────────────────────────


def _extract_json_robust(text: str) -> dict[str, Any] | None:
    """Try multiple strategies to extract a valid JSON object from LLM output.

    Handles: markdown code fences, leading/trailing whitespace, truncation
    mid-value, and trailing commas before the closing brace.
    """
    # Strip markdown fences
    if "```" in text:
        lines = text.split("\n")
        text = "\n".join(line for line in lines if not line.strip().startswith("```"))

    text = text.strip()

    # Strategy 1: direct parse
    try:
        result: dict[str, Any] = json.loads(text)
        return result
    except json.JSONDecodeError:
        pass

    # Strategy 2: find outermost { } bounds
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            result = json.loads(text[start : end + 1])
            return result
        except json.JSONDecodeError:
            pass

    # Strategy 3: truncation recovery — walk back from end to find a complete field
    if start != -1:
        for end_pos in range(len(text) - 1, start, -1):
            if text[end_pos] in (",", "\n"):
                candidate = text[start:end_pos].rstrip(", \n") + "\n}"
                try:
                    result = json.loads(candidate)
                    return result
                except json.JSONDecodeError:
                    continue

    return None


def _build_messages(
    company_name: str,
    combined: str,
    provider_name: str,
) -> list[Any]:
    """Build the message list, adding Anthropic prompt-cache header when applicable.

    Anthropic supports cache_control on content blocks — caching the long system
    prompt avoids re-tokenising it on every call (saves ~85% of input token cost
    for the system prompt portion when the same prompt is reused within 5 minutes).
    """
    user_content = (
        f"<company>{company_name}</company>\n\n"
        f"<snippets>\n{combined}\n</snippets>"
    )

    if provider_name == "anthropic" and HumanMessage is not None:
        # Use raw dict messages so we can attach cache_control to the system block.
        return [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": _LLM_SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        "type": "text",
                        "text": user_content,
                    },
                ],
            }
        ]

    if HumanMessage is None or SystemMessage is None:
        return []
    return [
        SystemMessage(content=_LLM_SYSTEM_PROMPT),
        HumanMessage(content=user_content),
    ]


def _extract_tokens(response: Any) -> tuple[int, int]:
    """Extract (input_tokens, output_tokens) from a LangChain response object."""
    usage = getattr(response, "usage_metadata", None)
    if usage:
        input_t = getattr(usage, "input_tokens", 0) or 0
        output_t = getattr(usage, "output_tokens", 0) or 0
        return int(input_t), int(output_t)
    # Fallback: rough character-based estimate (4 chars ≈ 1 token)
    content = getattr(response, "content", "") or ""
    return 0, max(1, len(str(content)) // 4)


def _llm_extract_structured(
    llm: Any,
    model_label: str,
    company_name: str,
    snippets: list[str],
    tracker: CostTracker | None = None,
    provider_cfg: LLMProviderConfig | None = None,
) -> dict[str, Any]:
    """Call LLM with retry, cost tracking, and prompt caching; return extracted facts dict."""
    combined = "\n".join(snippets[:40])[:5000]
    provider_name = (provider_cfg.name if provider_cfg else "").lower()
    messages = _build_messages(company_name, combined, provider_name)
    if not messages:
        return {}

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = llm.invoke(messages)

            # ── Cost tracking ────────────────────────────────────────────
            if tracker is not None and provider_cfg is not None:
                input_t, output_t = _extract_tokens(response)
                cost = estimate_cost(
                    input_t,
                    output_t,
                    provider_cfg.cost_per_1k_input_usd,
                    provider_cfg.cost_per_1k_output_usd,
                )
                record = CostRecord(
                    model=model_label,
                    input_tokens=input_t,
                    output_tokens=output_t,
                    cost_usd=cost,
                    provider=provider_cfg.name,
                )
                # Note: caller receives updated tracker via return value below;
                # we store it locally only to log it here.
                updated = tracker.add(record)
                logger.info(
                    "llm_call: model=%s input_tokens=%d output_tokens=%d "
                    "call_cost_usd=%.6f cumulative_usd=%.6f",
                    model_label,
                    input_t,
                    output_t,
                    cost,
                    updated.total_cost,
                )

            content = response.content
            if isinstance(content, str):
                parsed = _extract_json_robust(content)
                if parsed is None:
                    logger.warning("LLM extraction failed: could not parse JSON from response")
                    return {}
                parsed["_model_label"] = model_label
                return parsed
            return {}

        except Exception as exc:
            if _is_fatal(exc):
                logger.error("llm_call fatal error (no retry): %s", exc)
                return {}
            if _is_transient(exc):
                last_exc = exc
                if attempt < _MAX_RETRIES - 1:
                    sleep = _RETRY_BASE_SLEEP * (2**attempt)
                    logger.warning(
                        "llm_call transient error (attempt %d/%d, retry in %.1fs): %s",
                        attempt + 1,
                        _MAX_RETRIES,
                        sleep,
                        exc,
                    )
                    time.sleep(sleep)
                    continue
            else:
                # Unknown error type — log and give up
                logger.warning("LLM extraction failed: %s", exc)
                return {}

    logger.warning("LLM extraction exhausted retries: %s", last_exc)
    return {}

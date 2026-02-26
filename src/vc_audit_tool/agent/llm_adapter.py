"""LLM provider adapter for the research agent.

Tries providers in priority order:
  Google Gemini Flash → OpenAI GPT-4o-mini → Anthropic Haiku → Ollama → None
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

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


# ── System prompt ────────────────────────────────────────────────────────

_LLM_SYSTEM_PROMPT = (
    "You are a financial analyst. From the search snippets, extract ONLY confirmed facts.\n"
    "Return ONLY a JSON object with these keys:\n"
    "- last_post_money_valuation: number or null (USD, most recent valuation)\n"
    "- last_round_date: string or null (ISO date YYYY-MM-DD or 'Month YYYY')\n"
    "- last_round_amount_raised: number or null (USD raised in last round)\n"
    "- revenue_ltm: number or null (USD annual revenue or ARR)\n"
    "- company_description: string or null (1-2 sentences)\n"
    "- valuation_signals: list of objects [{amount_usd, source, date, type}] "
    "  where type is one of: post_money, secondary_market, analyst_estimate\n"
    "NEVER guess. Return null if uncertain. JSON only, no markdown."
)


# ── Provider selection ───────────────────────────────────────────────────


def _get_llm() -> tuple[Any, str]:
    """Return (llm_instance, model_label) for the highest-priority available provider."""
    if os.environ.get("GOOGLE_API_KEY") and ChatGoogleGenerativeAI is not None:
        try:
            model = os.environ.get("GOOGLE_MODEL", "gemini-2.5-flash")
            llm: Any = ChatGoogleGenerativeAI(model=model, temperature=0, max_output_tokens=1024)
            return llm, f"google/{model}"
        except Exception as exc:
            logger.warning("Google Gemini init failed (%s)", exc)

    if os.environ.get("OPENAI_API_KEY") and ChatOpenAI is not None:
        try:
            model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
            llm = ChatOpenAI(model=model, temperature=0)
            return llm, f"openai/{model}"
        except Exception as exc:
            logger.warning("OpenAI init failed (%s)", exc)

    if os.environ.get("ANTHROPIC_API_KEY") and ChatAnthropic is not None:
        try:
            model = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-20241022")
            llm = ChatAnthropic(model=model, temperature=0, max_tokens=1024)
            return llm, f"anthropic/{model}"
        except Exception as exc:
            logger.warning("Anthropic init failed (%s)", exc)

    ollama_model = os.environ.get("OLLAMA_MODEL", "")
    if ollama_model and ChatOllama is not None:
        try:
            base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
            llm = ChatOllama(model=ollama_model, base_url=base_url, temperature=0, num_predict=512)
            return llm, f"ollama/{ollama_model}"
        except Exception as exc:
            logger.warning("Ollama init failed (%s)", exc)

    return None, ""


# ── Structured extraction ────────────────────────────────────────────────


def _llm_extract_structured(
    llm: Any,
    model_label: str,
    company_name: str,
    snippets: list[str],
) -> dict[str, Any]:
    """Call LLM and return extracted facts dict."""
    try:
        combined = "\n".join(snippets[:40])[:5000]
        response = llm.invoke(
            [
                SystemMessage(content=_LLM_SYSTEM_PROMPT),
                HumanMessage(content=f"Company: {company_name}\n\nSnippets:\n{combined}"),
            ]
        )
        content = response.content
        if isinstance(content, str):
            text = content.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
            parsed: dict[str, Any] = json.loads(text)
            parsed["_model_label"] = model_label
            return parsed
    except Exception as exc:
        logger.warning("LLM extraction failed: %s", exc)
    return {}

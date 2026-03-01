"""Utility for loading private company discount defaults from YAML config.

Provides a single source of truth for discount defaults with hardcoded
fallbacks when the config file is missing or malformed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Hardcoded fallback defaults (mirrors config/methodology_rules_v1.yaml)
_FALLBACK_DEFAULTS: dict[str, Any] = {
    "comparable_companies": 25,
    "last_round_market_adjusted": 10,
    "last_round_multiple_ratchet": 25,
    "direct_valuation": {
        "has_secondary_evidence": 10,
        "no_secondary_evidence": 20,
    },
}

_MAX_ALLOWED = 50


def _load_discount_config() -> dict[str, Any]:
    """Load private_company_discount section from methodology_rules_v1.yaml.

    Returns the defaults dict, falling back to _FALLBACK_DEFAULTS on any error.
    """
    try:
        import yaml

        # Search for config relative to this file's location
        candidates = [
            Path(__file__).parent.parent.parent.parent / "config" / "methodology_rules_v1.yaml",
            Path(__file__).parent.parent.parent.parent.parent
            / "config"
            / "methodology_rules_v1.yaml",
        ]
        config_path: Path | None = next((p for p in candidates if p.exists()), None)
        if config_path is None:
            logger.debug("discount_config: YAML not found, using fallback defaults")
            return _FALLBACK_DEFAULTS
        with config_path.open() as f:
            data = yaml.safe_load(f)
        discount_section = data.get("private_company_discount", {})
        loaded: dict[str, Any] = discount_section.get("defaults", {})
        if not loaded:
            return _FALLBACK_DEFAULTS
        return loaded
    except Exception as exc:
        logger.warning("discount_config: failed to load YAML defaults: %s", exc)
        return _FALLBACK_DEFAULTS


def get_discount_default(methodology: str, *, has_secondary_evidence: bool = False) -> float:
    """Return the default private company discount % for a methodology.

    Args:
        methodology: One of the methodology slug strings.
        has_secondary_evidence: For direct_valuation only — True when the
            evidence package contains secondary_market or post_money_fresh signals,
            which warrants a lower discount.

    Returns:
        Default discount as a percentage (e.g. 25.0 for 25%).
        Returns 0.0 for exempt methodologies (scorecard, berkus) and unknown slugs.
    """
    defaults = _load_discount_config()

    if methodology == "direct_valuation":
        dv = defaults.get("direct_valuation", _FALLBACK_DEFAULTS["direct_valuation"])
        if isinstance(dv, dict):
            key = "has_secondary_evidence" if has_secondary_evidence else "no_secondary_evidence"
            return float(dv.get(key, _FALLBACK_DEFAULTS["direct_valuation"][key]))
        return float(dv)

    raw = defaults.get(methodology)
    if raw is None:
        fallback = _FALLBACK_DEFAULTS.get(methodology)
        if isinstance(fallback, dict):
            return float(fallback.get("no_secondary_evidence", 20))
        return float(fallback) if fallback is not None else 0.0
    return float(raw)


def clamp_discount(value: float, max_pct: float = float(_MAX_ALLOWED)) -> float:
    """Clamp a user-supplied discount to [0, max_pct]."""
    return max(0.0, min(float(max_pct), float(value)))

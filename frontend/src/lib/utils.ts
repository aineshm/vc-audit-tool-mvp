export function formatMoney(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value >= 1e12) return `$${(value / 1e12).toFixed(2)}T`;
  if (value >= 1e9) return `$${(value / 1e9).toFixed(2)}B`;
  if (value >= 1e6) return `$${(value / 1e6).toFixed(1)}M`;
  return `$${value.toLocaleString()}`;
}

export function formatDate(isoString: string | null | undefined): string {
  if (!isoString) return "—";
  try {
    return new Date(isoString).toLocaleDateString("en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return isoString;
  }
}

export function tierBadgeClass(tier: string | null): string {
  if (!tier)
    return "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300";
  if (tier.includes("tier_1"))
    return "bg-emerald-50 text-emerald-700 dark:bg-emerald-900 dark:text-emerald-300";
  if (tier.includes("tier_2"))
    return "bg-teal-50 text-teal-700 dark:bg-teal-900 dark:text-teal-300";
  if (tier.includes("tier_3"))
    return "bg-yellow-50 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300";
  if (tier.includes("tier_5"))
    return "bg-red-50 text-red-700 dark:bg-red-900 dark:text-red-300";
  if (tier.includes("llm"))
    return "bg-purple-50 text-purple-700 dark:bg-purple-900 dark:text-purple-300";
  return "bg-orange-50 text-orange-700 dark:bg-orange-900 dark:text-orange-300";
}

export function tierLabel(tier: string | null): string {
  const labels: Record<string, string> = {
    tier_1_premier_financial: "Premier",
    tier_2_specialist_tech: "Specialist",
    tier_3_general_press: "General",
    tier_4_unrecognized: "Unverified",
    tier_5_low_quality: "Low Quality",
    tier_llm_synthetic: "LLM",
  };
  return tier ? (labels[tier] ?? tier) : "Unknown";
}

export function methodologyLabel(slug: string): string {
  const labels: Record<string, string> = {
    comparable_companies: "Comparable Companies",
    last_round_market_adjusted: "Last Round (Mkt. Adj.)",
    last_round_multiple_ratchet: "Multiple Ratchet",
    direct_valuation: "Direct Valuation",
    scorecard: "Scorecard",
    berkus: "Berkus",
  };
  return labels[slug] ?? slug;
}

export function getFairValue(vr: {
  fair_value?: number;
  estimated_fair_value?: { amount: number };
}): number | null {
  return vr.fair_value ?? vr.estimated_fair_value?.amount ?? null;
}

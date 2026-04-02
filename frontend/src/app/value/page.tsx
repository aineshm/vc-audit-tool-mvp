"use client";

import { useState } from "react";
import { createDataService } from "@/lib/data-service";
import type { ValuationEnvelope } from "@/types/api";
import { getFairValue, methodologyLabel } from "@/lib/utils";
import FairValueDisplay from "@/components/results/FairValueDisplay";
import ConfidenceBar from "@/components/results/ConfidenceBar";
import DerivationSteps from "@/components/results/DerivationSteps";

const METHODOLOGY_TEMPLATES: Record<string, Record<string, unknown>> = {
  comparable_companies: {
    revenue_ltm: 100_000_000,
    sector: "enterprise_software",
    private_company_discount_pct: 25,
  },
  last_round_market_adjusted: {
    last_post_money_valuation: 500_000_000,
    last_round_date: "2024-06-01",
    public_index: "NASDAQ_COMPOSITE",
  },
  last_round_multiple_ratchet: {
    last_post_money_valuation: 500_000_000,
    last_round_date: "2024-06-01",
    revenue_ltm: 50_000_000,
    revenue_at_last_round: 30_000_000,
  },
  direct_valuation: {
    evidence_signals: [],
    consensus_strength: "MODERATE",
    private_company_discount_pct: 20,
  },
  scorecard: {
    regional_median_valuation: 2_000_000,
    team: 125,
    opportunity: 100,
    product: 110,
    competitive_env: 90,
    marketing: 100,
    funding_need: 100,
  },
  berkus: {
    sound_idea: 500_000,
    prototype: 500_000,
    management: 500_000,
    strategic_relationships: 500_000,
    rollout: 500_000,
  },
};

const today = new Date().toISOString().slice(0, 10);

export default function ValuePage() {
  const [companyName, setCompanyName] = useState("");
  const [methodology, setMethodology] = useState("last_round_market_adjusted");
  const [asOfDate, setAsOfDate] = useState(today);
  const [inputsJson, setInputsJson] = useState(
    JSON.stringify(
      METHODOLOGY_TEMPLATES["last_round_market_adjusted"],
      null,
      2
    )
  );
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ValuationEnvelope | null>(null);

  const handleMethodologyChange = (m: string) => {
    setMethodology(m);
    setInputsJson(
      JSON.stringify(METHODOLOGY_TEMPLATES[m] ?? {}, null, 2)
    );
    setJsonError(null);
  };

  const handleInputsChange = (val: string) => {
    setInputsJson(val);
    try {
      JSON.parse(val);
      setJsonError(null);
    } catch {
      setJsonError("Invalid JSON");
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    let inputs: Record<string, unknown>;
    try {
      inputs = JSON.parse(inputsJson) as Record<string, unknown>;
    } catch {
      setJsonError("Invalid JSON — fix before submitting");
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const svc = createDataService();
      const res = await svc.runManualValuation({
        company_name: companyName.trim(),
        methodology,
        as_of_date: asOfDate,
        inputs,
      });
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  };

  const fv = result?.valuation_result
    ? getFairValue(result.valuation_result)
    : null;

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div>
        <h2 className="text-xl font-bold text-ink dark:text-sand-100">
          Manual Valuation
        </h2>
        <p className="text-sm text-ink-light dark:text-gray-400 mt-1">
          Structured inputs for a specific valuation methodology.
        </p>
      </div>

      <form
        onSubmit={handleSubmit}
        className="bg-white dark:bg-gray-900 border border-sand-200 dark:border-gray-800 rounded-xl p-5 space-y-4"
      >
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-bold text-ink-light dark:text-gray-400 uppercase tracking-wide mb-1">
              Company Name <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              required
              value={companyName}
              onChange={(e) => setCompanyName(e.target.value)}
              placeholder="e.g. Acme Corp"
              className="w-full px-3 py-2 text-sm bg-sand-50 dark:bg-gray-800 border border-sand-200 dark:border-gray-700 rounded-lg focus:outline-none focus:ring-2 focus:ring-teal-500 text-ink dark:text-sand-100"
            />
          </div>
          <div>
            <label className="block text-xs font-bold text-ink-light dark:text-gray-400 uppercase tracking-wide mb-1">
              As-of Date <span className="text-red-500">*</span>
            </label>
            <input
              type="date"
              required
              value={asOfDate}
              onChange={(e) => setAsOfDate(e.target.value)}
              className="w-full px-3 py-2 text-sm bg-sand-50 dark:bg-gray-800 border border-sand-200 dark:border-gray-700 rounded-lg focus:outline-none focus:ring-2 focus:ring-teal-500 text-ink dark:text-sand-100"
            />
          </div>
        </div>

        <div>
          <label className="block text-xs font-bold text-ink-light dark:text-gray-400 uppercase tracking-wide mb-1">
            Methodology <span className="text-red-500">*</span>
          </label>
          <select
            value={methodology}
            onChange={(e) => handleMethodologyChange(e.target.value)}
            className="w-full px-3 py-2 text-sm bg-sand-50 dark:bg-gray-800 border border-sand-200 dark:border-gray-700 rounded-lg focus:outline-none focus:ring-2 focus:ring-teal-500 text-ink dark:text-sand-100"
          >
            {Object.keys(METHODOLOGY_TEMPLATES).map((m) => (
              <option key={m} value={m}>
                {methodologyLabel(m)}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-xs font-bold text-ink-light dark:text-gray-400 uppercase tracking-wide mb-1">
            Inputs (JSON)
          </label>
          <textarea
            rows={10}
            value={inputsJson}
            onChange={(e) => handleInputsChange(e.target.value)}
            className={`w-full px-3 py-2 text-xs font-mono bg-sand-50 dark:bg-gray-800 border rounded-lg focus:outline-none focus:ring-2 focus:ring-teal-500 text-ink dark:text-sand-100 ${
              jsonError
                ? "border-red-400"
                : "border-sand-200 dark:border-gray-700"
            }`}
          />
          {jsonError && (
            <p className="text-xs text-red-500 mt-1">{jsonError}</p>
          )}
        </div>

        <button
          type="submit"
          disabled={loading || !companyName.trim() || !!jsonError}
          className="w-full py-2.5 bg-teal-700 hover:bg-teal-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-semibold rounded-lg transition-colors"
        >
          {loading ? "Computing…" : "Run Valuation"}
        </button>

        {error && (
          <p className="text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-950 rounded-lg p-3">
            {error}
          </p>
        )}
      </form>

      {result && (
        <div className="space-y-4">
          {fv != null && <FairValueDisplay value={fv} />}

          {result.valuation_result && (
            <div className="bg-white dark:bg-gray-900 border border-sand-200 dark:border-gray-800 rounded-xl p-4 space-y-4">
              <ConfidenceBar
                value={result.valuation_result.confidence_indicators.overall}
                label="Overall Confidence"
              />
              <DerivationSteps
                steps={result.valuation_result.derivation_steps}
              />
              {result.valuation_result.assumptions.length > 0 && (
                <div>
                  <h4 className="text-xs uppercase tracking-widest text-ink-light dark:text-gray-400 font-bold mb-2">
                    Assumptions
                  </h4>
                  <ul className="list-disc list-inside space-y-1">
                    {result.valuation_result.assumptions.map((a, i) => (
                      <li
                        key={i}
                        className="text-xs text-ink-light dark:text-gray-400"
                      >
                        {a}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createDataService } from "@/lib/data-service";
import type { ValuationEnvelope } from "@/types/api";
import { getFairValue } from "@/lib/utils";
import FairValueDisplay from "@/components/results/FairValueDisplay";
import ConfidenceBar from "@/components/results/ConfidenceBar";
import DerivationSteps from "@/components/results/DerivationSteps";
import EvidencePackageCard from "@/components/results/EvidencePackageCard";

const METHODOLOGIES = [
  { value: "comparable_companies", label: "Comparable Companies" },
  { value: "last_round_market_adjusted", label: "Last Round (Mkt. Adj.)" },
  { value: "last_round_multiple_ratchet", label: "Multiple Ratchet" },
  { value: "direct_valuation", label: "Direct Valuation" },
  { value: "scorecard", label: "Scorecard" },
  { value: "berkus", label: "Berkus" },
];

export default function ResearchPage() {
  const router = useRouter();
  const [companyName, setCompanyName] = useState("");
  const [asOfDate, setAsOfDate] = useState("");
  const [methodology, setMethodology] = useState("");
  const [hint, setHint] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ValuationEnvelope | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const svc = createDataService();
      const res = await svc.runResearch({
        company_name: companyName.trim(),
        as_of_date: asOfDate || undefined,
        methodology: methodology || undefined,
        description_hint: hint || undefined,
      });
      setResult(res);
      // Redirect to run detail page if we got a request_id back
      const runId = res.audit_metadata?.request_id;
      if (runId) {
        router.push(`/runs/${runId}`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  };

  const fv = result?.valuation_result
    ? getFairValue(result.valuation_result)
    : null;
  const evPkg = result?.research_metadata?.evidence_package;

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div>
        <h2 className="text-xl font-bold text-ink dark:text-sand-100">
          Research a Company
        </h2>
        <p className="text-sm text-ink-light dark:text-gray-400 mt-1">
          Automatically research and value a private company from its name.
        </p>
      </div>

      <form
        onSubmit={handleSubmit}
        className="bg-white dark:bg-gray-900 border border-sand-200 dark:border-gray-800 rounded-xl p-5 space-y-4"
      >
        <div>
          <label className="block text-xs font-bold text-ink-light dark:text-gray-400 uppercase tracking-wide mb-1">
            Company Name <span className="text-red-500">*</span>
          </label>
          <input
            type="text"
            required
            value={companyName}
            onChange={(e) => setCompanyName(e.target.value)}
            placeholder="e.g. Stripe, Anduril, Databricks"
            className="w-full px-3 py-2 text-sm bg-sand-50 dark:bg-gray-800 border border-sand-200 dark:border-gray-700 rounded-lg focus:outline-none focus:ring-2 focus:ring-teal-500 text-ink dark:text-sand-100"
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-bold text-ink-light dark:text-gray-400 uppercase tracking-wide mb-1">
              As-of Date (optional)
            </label>
            <input
              type="date"
              value={asOfDate}
              onChange={(e) => setAsOfDate(e.target.value)}
              className="w-full px-3 py-2 text-sm bg-sand-50 dark:bg-gray-800 border border-sand-200 dark:border-gray-700 rounded-lg focus:outline-none focus:ring-2 focus:ring-teal-500 text-ink dark:text-sand-100"
            />
          </div>
          <div>
            <label className="block text-xs font-bold text-ink-light dark:text-gray-400 uppercase tracking-wide mb-1">
              Methodology (optional)
            </label>
            <select
              value={methodology}
              onChange={(e) => setMethodology(e.target.value)}
              className="w-full px-3 py-2 text-sm bg-sand-50 dark:bg-gray-800 border border-sand-200 dark:border-gray-700 rounded-lg focus:outline-none focus:ring-2 focus:ring-teal-500 text-ink dark:text-sand-100"
            >
              <option value="">Auto-select</option>
              {METHODOLOGIES.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div>
          <label className="block text-xs font-bold text-ink-light dark:text-gray-400 uppercase tracking-wide mb-1">
            Description Hint (optional)
          </label>
          <input
            type="text"
            value={hint}
            onChange={(e) => setHint(e.target.value)}
            placeholder="e.g. B2B SaaS, payments infrastructure, Series C"
            className="w-full px-3 py-2 text-sm bg-sand-50 dark:bg-gray-800 border border-sand-200 dark:border-gray-700 rounded-lg focus:outline-none focus:ring-2 focus:ring-teal-500 text-ink dark:text-sand-100"
          />
        </div>

        <button
          type="submit"
          disabled={loading || !companyName.trim()}
          className="w-full py-2.5 bg-teal-700 hover:bg-teal-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-semibold rounded-lg transition-colors"
        >
          {loading ? "Researching…" : "Run Research"}
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
            </div>
          )}

          {evPkg && <EvidencePackageCard pkg={evPkg} />}
        </div>
      )}
    </div>
  );
}

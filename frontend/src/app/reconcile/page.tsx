"use client";

import { useState } from "react";
import { createDataService } from "@/lib/data-service";
import type { ReconciledEnvelope } from "@/types/api";
import { formatMoney, methodologyLabel } from "@/lib/utils";
import FairValueDisplay from "@/components/results/FairValueDisplay";

export default function ReconcilePage() {
  const [companyName, setCompanyName] = useState("");
  const [asOfDate, setAsOfDate] = useState("");
  const [hint, setHint] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ReconciledEnvelope | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const svc = createDataService();
      const res = await svc.runReconcile({
        company_name: companyName.trim(),
        as_of_date: asOfDate || undefined,
        description_hint: hint || undefined,
      });
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  };

  const rec = result?.reconciliation;

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div>
        <h2 className="text-xl font-bold text-ink dark:text-sand-100">
          Multi-Methodology Reconcile
        </h2>
        <p className="text-sm text-ink-light dark:text-gray-400 mt-1">
          Research a company and reconcile across multiple valuation
          methodologies.
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
              Description Hint (optional)
            </label>
            <input
              type="text"
              value={hint}
              onChange={(e) => setHint(e.target.value)}
              placeholder="e.g. B2B SaaS, Series C"
              className="w-full px-3 py-2 text-sm bg-sand-50 dark:bg-gray-800 border border-sand-200 dark:border-gray-700 rounded-lg focus:outline-none focus:ring-2 focus:ring-teal-500 text-ink dark:text-sand-100"
            />
          </div>
        </div>

        <button
          type="submit"
          disabled={loading || !companyName.trim()}
          className="w-full py-2.5 bg-teal-700 hover:bg-teal-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-semibold rounded-lg transition-colors"
        >
          {loading ? "Reconciling…" : "Run Reconciliation"}
        </button>

        {error && (
          <p className="text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-950 rounded-lg p-3">
            {error}
          </p>
        )}
      </form>

      {result && (
        <div className="space-y-4">
          {result.concluded_value != null && (
            <FairValueDisplay
              value={result.concluded_value.point_estimate}
              label="Concluded Value"
            />
          )}

          {rec && (
            <div className="bg-white dark:bg-gray-900 border border-sand-200 dark:border-gray-800 rounded-xl p-4 space-y-4">
              {result.concluded_value && (
                <p className="text-sm text-ink-light dark:text-gray-400">
                  Range:{" "}
                  <span className="font-mono font-bold text-teal-700 dark:text-teal-300">
                    {formatMoney(result.concluded_value.range_low)} –{" "}
                    {formatMoney(result.concluded_value.range_high)}
                  </span>
                  {rec.divergence_flag && (
                    <span className="ml-2 text-xs bg-yellow-50 text-yellow-700 px-2 py-0.5 rounded-full font-bold">
                      HIGH DIVERGENCE
                    </span>
                  )}
                </p>
              )}

              {rec.reconciliation_rationale && (
                <p className="text-xs text-ink-light dark:text-gray-400 italic">
                  {rec.reconciliation_rationale}
                </p>
              )}

              {rec.divergence_note && (
                <p className="text-xs text-yellow-700 dark:text-yellow-300 bg-yellow-50 dark:bg-yellow-950 rounded-lg p-2">
                  {rec.divergence_note}
                </p>
              )}

              <div>
                <h4 className="text-xs uppercase tracking-widest text-ink-light dark:text-gray-400 font-bold mb-2">
                  Methodology Breakdown
                </h4>
                <div className="space-y-2">
                  {(rec.methodology_weights ?? []).map((mr) => (
                    <div
                      key={mr.methodology}
                      className="flex items-center gap-3 py-2 border-t border-sand-100 dark:border-gray-800 first:border-0"
                    >
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-ink dark:text-sand-100">
                            {methodologyLabel(mr.methodology)}
                          </span>
                          {!mr.data_requirements_met && (
                            <span className="text-xs bg-red-50 text-red-600 px-1.5 py-0.5 rounded font-bold">
                              DATA GAP
                            </span>
                          )}
                        </div>
                        <p className="text-xs text-ink-light dark:text-gray-400 truncate">
                          {mr.rationale}
                        </p>
                      </div>
                      <div className="text-right shrink-0">
                        <p className="font-mono text-sm font-bold text-teal-700 dark:text-teal-300">
                          {mr.point_estimate != null
                            ? formatMoney(mr.point_estimate)
                            : "—"}
                        </p>
                        <p className="text-xs text-ink-light dark:text-gray-400">
                          w={Math.round(mr.weight * 100)}%
                        </p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

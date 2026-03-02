"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { createDataService } from "@/lib/data-service";
import type { RunSummary } from "@/types/api";
import { formatMoney, formatDate, methodologyLabel } from "@/lib/utils";

export default function RunsPage() {
  const pathname = usePathname();
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchRuns = useCallback(() => {
    setLoading(true);
    const svc = createDataService();
    svc
      .listRuns()
      .then(setRuns)
      .catch((err: unknown) =>
        setError(
          err instanceof Error ? err.message : "Failed to load runs"
        )
      )
      .finally(() => setLoading(false));
  }, []);

  // Refetch every time user navigates to this page
  useEffect(fetchRuns, [pathname, fetchRuns]);

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div>
        <h2 className="text-xl font-bold text-ink dark:text-sand-100">
          Run History
        </h2>
        <p className="text-sm text-ink-light dark:text-gray-400 mt-1">
          All past valuation runs.
        </p>
      </div>

      <div className="bg-white dark:bg-gray-900 border border-sand-200 dark:border-gray-800 rounded-xl overflow-hidden">
        {loading ? (
          <p className="p-6 text-sm text-ink-light dark:text-gray-400">
            Loading…
          </p>
        ) : error ? (
          <p className="p-6 text-sm text-ink-light dark:text-gray-400">
            {error} — run history requires the FastAPI backend with a{" "}
            <code className="font-mono text-xs">/api/runs</code> endpoint.
          </p>
        ) : runs.length === 0 ? (
          <p className="p-6 text-sm text-ink-light dark:text-gray-400">
            No runs yet.{" "}
            <Link
              href="/research"
              className="text-teal-600 dark:text-teal-400 hover:underline"
            >
              Start a research run →
            </Link>
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-sand-100 dark:border-gray-800 text-xs text-ink-light dark:text-gray-400 uppercase tracking-wide">
                <th className="text-left px-4 py-3">Company</th>
                <th className="text-left px-4 py-3">Methodology</th>
                <th className="text-left px-4 py-3">As-of</th>
                <th className="text-right px-4 py-3">Fair Value</th>
                <th className="text-right px-4 py-3">Run At</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr
                  key={run.request_id}
                  className="border-b border-sand-50 dark:border-gray-800 last:border-0 hover:bg-sand-50 dark:hover:bg-gray-800"
                >
                  <td className="px-4 py-2.5 font-medium text-ink dark:text-sand-100">
                    <Link
                      href={`/runs/${run.request_id}`}
                      className="hover:text-teal-700 dark:hover:text-teal-300"
                    >
                      {run.company_name}
                    </Link>
                  </td>
                  <td className="px-4 py-2.5 text-ink-light dark:text-gray-400">
                    {methodologyLabel(run.methodology)}
                  </td>
                  <td className="px-4 py-2.5 text-ink-light dark:text-gray-400">
                    {formatDate(run.as_of_date)}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-teal-700 dark:text-teal-300">
                    {formatMoney(run.fair_value)}
                  </td>
                  <td className="px-4 py-2.5 text-right text-ink-light dark:text-gray-400">
                    {formatDate(run.generated_at_utc)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

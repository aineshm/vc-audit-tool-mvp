"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { createDataService } from "@/lib/data-service";
import type { RunSummary } from "@/types/api";
import { formatMoney, formatDate, methodologyLabel } from "@/lib/utils";

const QUICK_ACTIONS = [
  {
    href: "/research",
    label: "Research a Company",
    desc: "Auto-research + valuation from company name",
    icon: "◎",
  },
  {
    href: "/value",
    label: "Manual Valuation",
    desc: "Structured inputs for a specific methodology",
    icon: "⧖",
  },
  {
    href: "/reconcile",
    label: "Reconcile",
    desc: "Multi-methodology reconciled valuation",
    icon: "⊕",
  },
];

export default function DashboardPage() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runsError, setRunsError] = useState(false);

  useEffect(() => {
    const svc = createDataService();
    svc
      .listRuns()
      .then(setRuns)
      .catch(() => setRunsError(true));
  }, []);

  return (
    <div className="max-w-4xl mx-auto space-y-8">
      <div>
        <h2 className="text-2xl font-bold text-ink dark:text-sand-100">
          Valuation Workbench
        </h2>
        <p className="text-sm text-ink-light dark:text-gray-400 mt-1">
          Auditable, deterministic valuations for venture-backed private
          companies.
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        {QUICK_ACTIONS.map(({ href, label, desc, icon }) => (
          <Link
            key={href}
            href={href}
            className="block p-4 bg-white dark:bg-gray-900 border border-sand-200 dark:border-gray-800 rounded-xl hover:border-teal-300 dark:hover:border-teal-700 transition-colors group"
          >
            <div className="text-2xl mb-2">{icon}</div>
            <h3 className="font-semibold text-sm text-ink dark:text-sand-100 group-hover:text-teal-700 dark:group-hover:text-teal-300 transition-colors">
              {label}
            </h3>
            <p className="text-xs text-ink-light dark:text-gray-400 mt-0.5">
              {desc}
            </p>
          </Link>
        ))}
      </div>

      <div className="bg-white dark:bg-gray-900 border border-sand-200 dark:border-gray-800 rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-sand-100 dark:border-gray-800">
          <h3 className="text-xs uppercase tracking-widest text-ink-light dark:text-gray-400 font-bold">
            Recent Runs
          </h3>
        </div>

        {runsError ? (
          <p className="p-4 text-sm text-ink-light dark:text-gray-400">
            Run history unavailable — start the FastAPI backend to enable.
          </p>
        ) : runs.length === 0 ? (
          <p className="p-4 text-sm text-ink-light dark:text-gray-400">
            No runs yet. Start a valuation above.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-sand-100 dark:border-gray-800 text-xs text-ink-light dark:text-gray-400 uppercase tracking-wide">
                <th className="text-left px-4 py-2">Company</th>
                <th className="text-left px-4 py-2">Methodology</th>
                <th className="text-left px-4 py-2">Date</th>
                <th className="text-right px-4 py-2">Fair Value</th>
              </tr>
            </thead>
            <tbody>
              {runs.slice(0, 10).map((run) => (
                <tr
                  key={run.request_id}
                  className="border-b border-sand-50 dark:border-gray-800 last:border-0 hover:bg-sand-50 dark:hover:bg-gray-800"
                >
                  <td className="px-4 py-2 font-medium text-ink dark:text-sand-100">
                    <Link
                      href={`/runs/${run.request_id}`}
                      className="hover:text-teal-700 dark:hover:text-teal-300"
                    >
                      {run.company_name}
                    </Link>
                  </td>
                  <td className="px-4 py-2 text-ink-light dark:text-gray-400">
                    {methodologyLabel(run.methodology)}
                  </td>
                  <td className="px-4 py-2 text-ink-light dark:text-gray-400">
                    {formatDate(run.generated_at_utc)}
                  </td>
                  <td className="px-4 py-2 text-right font-mono text-teal-700 dark:text-teal-300">
                    {formatMoney(run.fair_value)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {runs.length > 0 && (
          <div className="px-4 py-2 border-t border-sand-100 dark:border-gray-800">
            <Link
              href="/runs"
              className="text-xs text-teal-600 dark:text-teal-400 hover:underline"
            >
              View all runs →
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}

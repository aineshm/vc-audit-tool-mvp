"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { createDataService } from "@/lib/data-service";
import type { ValuationEnvelope } from "@/types/api";
import { getFairValue, methodologyLabel } from "@/lib/utils";
import FairValueDisplay from "@/components/results/FairValueDisplay";
import ConfidenceBar from "@/components/results/ConfidenceBar";
import DerivationSteps from "@/components/results/DerivationSteps";
import EvidencePackageCard from "@/components/results/EvidencePackageCard";

export default function RunDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [data, setData] = useState<ValuationEnvelope | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const svc = createDataService();
    svc
      .getRunById(id)
      .then((res) => {
        if (!res) setError("Run not found");
        else setData(res);
      })
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : "Failed to load run")
      )
      .finally(() => setLoading(false));
  }, [id]);

  const fv = data?.valuation_result
    ? getFairValue(data.valuation_result)
    : null;
  const evPkg = data?.research_metadata?.evidence_package;

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <Link
        href="/runs"
        className="inline-block text-sm text-ink-light dark:text-gray-400 hover:text-teal-700 dark:hover:text-teal-300"
      >
        ← Back to runs
      </Link>

      {loading && (
        <p className="text-sm text-ink-light dark:text-gray-400">Loading…</p>
      )}
      {error && (
        <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
      )}

      {data && (
        <>
          <div>
            <h2 className="text-xl font-bold text-ink dark:text-sand-100">
              {data.research_metadata?.company_name ?? id}
            </h2>
            {data.valuation_result && (
              <p className="text-sm text-ink-light dark:text-gray-400 mt-1">
                {methodologyLabel(data.valuation_result.methodology)}
                {data.audit_metadata?.as_of_date &&
                  ` · as of ${data.audit_metadata.as_of_date}`}
              </p>
            )}
          </div>

          {fv != null && <FairValueDisplay value={fv} />}

          {data.valuation_result && (
            <div className="bg-white dark:bg-gray-900 border border-sand-200 dark:border-gray-800 rounded-xl p-4 space-y-4">
              <ConfidenceBar
                value={data.valuation_result.confidence_indicators.overall}
                label="Overall Confidence"
              />
              <DerivationSteps steps={data.valuation_result.derivation_steps} />
              {data.valuation_result.assumptions.length > 0 && (
                <div>
                  <h4 className="text-xs uppercase tracking-widest text-ink-light dark:text-gray-400 font-bold mb-2">
                    Assumptions
                  </h4>
                  <ul className="list-disc list-inside space-y-1">
                    {data.valuation_result.assumptions.map((a, i) => (
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

          {evPkg && <EvidencePackageCard pkg={evPkg} />}

          {data.audit_metadata && (
            <div className="border-t border-sand-100 dark:border-gray-800 pt-4 space-y-0.5">
              <p className="text-xs text-ink-light dark:text-gray-500">
                Request ID:{" "}
                <span className="font-mono">{data.audit_metadata.request_id}</span>
              </p>
              <p className="text-xs text-ink-light dark:text-gray-500">
                Engine:{" "}
                <span className="font-mono">
                  {data.audit_metadata.engine_version}
                </span>
              </p>
              <p className="text-xs text-ink-light dark:text-gray-500">
                Generated: {data.audit_metadata.generated_at_utc}
              </p>
            </div>
          )}
        </>
      )}
    </div>
  );
}

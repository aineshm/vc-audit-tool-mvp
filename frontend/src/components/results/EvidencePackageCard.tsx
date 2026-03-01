import type { EvidencePackage, ValuationEvidence } from "@/types/api";
import { formatMoney } from "@/lib/utils";
import ConfidenceBar from "./ConfidenceBar";
import SourceReliabilityBadge from "./SourceReliabilityBadge";

interface Props {
  pkg: EvidencePackage;
}

export default function EvidencePackageCard({ pkg }: Props) {
  return (
    <div className="bg-white dark:bg-gray-900 border border-sand-200 dark:border-gray-800 rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs uppercase tracking-widest text-ink-light dark:text-gray-400 font-bold">
          Evidence Package
        </h3>
        <span
          className={`text-xs px-2 py-0.5 rounded-full font-bold uppercase ${
            pkg.consensus_strength === "STRONG"
              ? "bg-emerald-50 text-emerald-700"
              : pkg.consensus_strength === "MODERATE"
                ? "bg-yellow-50 text-yellow-700"
                : "bg-red-50 text-red-600"
          }`}
        >
          {pkg.consensus_strength}
        </span>
      </div>

      {pkg.consensus_valuation != null && (
        <p className="text-sm text-ink-light dark:text-gray-400 mb-3">
          Consensus:{" "}
          <span className="font-mono font-bold text-teal-700 dark:text-teal-300">
            {formatMoney(pkg.consensus_valuation)}
          </span>
          <span className="ml-1 text-xs">({pkg.evidence_count} signals)</span>
        </p>
      )}

      <div className="space-y-2">
        {pkg.evidence.map((ev, i) => (
          <EvidenceRow key={i} ev={ev} />
        ))}
      </div>
    </div>
  );
}

function EvidenceRow({ ev }: { ev: ValuationEvidence }) {
  return (
    <div className="flex items-start gap-3 py-2 border-t border-sand-100 dark:border-gray-800 first:border-0">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5 flex-wrap">
          <span className="font-mono text-sm font-bold text-ink dark:text-sand-100">
            {formatMoney(ev.amount_usd)}
          </span>
          <span className="text-xs text-ink-light dark:text-gray-400">
            {ev.evidence_type.replace(/_/g, " ")}
          </span>
          <SourceReliabilityBadge tier={ev.source_reliability_tier} />
        </div>
        <p className="text-xs text-ink-light dark:text-gray-500 truncate">
          {ev.source_title ?? "Unknown source"}
          {ev.date_mentioned ? ` · ${ev.date_mentioned}` : ""}
        </p>
      </div>
      <div className="w-20 shrink-0 pt-1">
        <ConfidenceBar value={ev.confidence} />
        <p className="text-xs text-center font-mono text-ink-light dark:text-gray-400 mt-0.5">
          {Math.round(ev.confidence * 100)}%
        </p>
      </div>
    </div>
  );
}

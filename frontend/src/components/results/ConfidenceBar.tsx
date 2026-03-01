interface Props {
  value: number; // 0.0 to 1.0
  label?: string;
}

export default function ConfidenceBar({ value, label }: Props) {
  const pct = Math.round(Math.min(1, Math.max(0, value)) * 100);
  const color =
    pct >= 75 ? "bg-emerald-500" : pct >= 50 ? "bg-yellow-400" : "bg-red-400";

  return (
    <div>
      {label && (
        <div className="flex justify-between text-xs text-ink-light dark:text-gray-400 mb-1">
          <span>{label}</span>
          <span className="font-mono">{pct}%</span>
        </div>
      )}
      <div className="w-full h-2 bg-sand-200 dark:bg-gray-700 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

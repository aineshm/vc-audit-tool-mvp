import { formatMoney } from "@/lib/utils";

interface Props {
  value: number | null;
  label?: string;
}

export default function FairValueDisplay({
  value,
  label = "Fair Value",
}: Props) {
  return (
    <div className="text-center py-4">
      <p className="text-xs uppercase tracking-widest text-ink-light dark:text-gray-400 mb-1">
        {label}
      </p>
      <p className="text-4xl font-bold text-teal-700 dark:text-teal-300 font-mono">
        {formatMoney(value)}
      </p>
    </div>
  );
}

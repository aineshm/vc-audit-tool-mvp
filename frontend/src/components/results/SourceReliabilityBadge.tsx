import { tierBadgeClass, tierLabel } from "@/lib/utils";

interface Props {
  tier: string | null;
}

export default function SourceReliabilityBadge({ tier }: Props) {
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-bold uppercase tracking-wide ${tierBadgeClass(tier)}`}
    >
      {tierLabel(tier)}
    </span>
  );
}

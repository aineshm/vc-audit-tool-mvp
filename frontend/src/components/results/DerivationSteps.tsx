"use client";

import { useState } from "react";

interface Props {
  steps: string[];
}

export default function DerivationSteps({ steps }: Props) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? steps : steps.slice(0, 3);

  return (
    <div>
      <h3 className="text-xs uppercase tracking-widest text-ink-light dark:text-gray-400 font-bold mb-2">
        Derivation
      </h3>
      <ol className="space-y-1.5">
        {visible.map((step, i) => (
          <li key={i} className="flex gap-2 text-sm">
            <span className="shrink-0 w-5 h-5 rounded-full bg-teal-50 dark:bg-teal-900 text-teal-700 dark:text-teal-300 text-xs flex items-center justify-center font-bold">
              {i + 1}
            </span>
            <span className="text-ink dark:text-sand-200 font-mono text-xs leading-relaxed">
              {step}
            </span>
          </li>
        ))}
      </ol>
      {steps.length > 3 && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="mt-2 text-xs text-teal-600 dark:text-teal-400 hover:underline"
        >
          {expanded ? "Show less" : `Show ${steps.length - 3} more steps`}
        </button>
      )}
    </div>
  );
}

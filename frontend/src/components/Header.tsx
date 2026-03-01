"use client";

import { useEffect, useState } from "react";

export default function Header() {
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [isDark, setIsDark] = useState(false);

  useEffect(() => {
    const checkHealth = async () => {
      try {
        const res = await fetch("/health");
        setHealthy(res.ok);
      } catch {
        setHealthy(false);
      }
    };
    checkHealth();
    const interval = setInterval(checkHealth, 30_000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    setIsDark(mq.matches);
    document.documentElement.classList.toggle("dark", mq.matches);
    const handler = (e: MediaQueryListEvent) => {
      setIsDark(e.matches);
      document.documentElement.classList.toggle("dark", e.matches);
    };
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

  const toggleDark = () => {
    const next = !isDark;
    setIsDark(next);
    document.documentElement.classList.toggle("dark", next);
  };

  return (
    <header className="h-12 shrink-0 bg-white dark:bg-gray-900 border-b border-sand-200 dark:border-gray-800 flex items-center justify-between px-6">
      <h1 className="text-sm font-semibold text-ink dark:text-sand-100">
        Valuation Workbench
      </h1>
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-1.5">
          <span
            className={`w-2 h-2 rounded-full ${
              healthy === null
                ? "bg-gray-400"
                : healthy
                  ? "bg-emerald-500"
                  : "bg-red-500"
            }`}
          />
          <span className="text-xs text-ink-light dark:text-gray-400">
            {healthy === null ? "Checking…" : healthy ? "Live" : "Offline"}
          </span>
        </div>
        <button
          onClick={toggleDark}
          className="text-xs px-2 py-1 rounded border border-sand-300 dark:border-gray-700 text-ink-light dark:text-gray-400 hover:bg-sand-100 dark:hover:bg-gray-800"
        >
          {isDark ? "☀ Light" : "☾ Dark"}
        </button>
      </div>
    </header>
  );
}

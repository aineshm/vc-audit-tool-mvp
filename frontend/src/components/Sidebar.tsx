"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_LINKS = [
  { href: "/", label: "Dashboard", icon: "⊞" },
  { href: "/research", label: "Research", icon: "◎" },
  { href: "/value", label: "Valuation", icon: "⧖" },
  { href: "/reconcile", label: "Reconcile", icon: "⊕" },
  { href: "/runs", label: "Run History", icon: "≡" },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-56 shrink-0 bg-white dark:bg-gray-900 border-r border-sand-200 dark:border-gray-800 flex flex-col">
      <div className="px-4 py-5 border-b border-sand-200 dark:border-gray-800">
        <span className="font-bold text-sm tracking-widest text-teal-700 dark:text-teal-300 uppercase">
          VC Audit
        </span>
      </div>

      <nav className="flex-1 px-2 py-4 space-y-1">
        {NAV_LINKS.map(({ href, label, icon }) => {
          const active = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              className={`flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                active
                  ? "bg-teal-50 dark:bg-teal-900 text-teal-700 dark:text-teal-300"
                  : "text-ink-light dark:text-gray-400 hover:bg-sand-100 dark:hover:bg-gray-800"
              }`}
            >
              <span className="text-base">{icon}</span>
              {label}
            </Link>
          );
        })}
      </nav>

      <div className="px-4 py-3 border-t border-sand-200 dark:border-gray-800">
        <p className="text-xs text-ink-light dark:text-gray-500">v0.1.0</p>
      </div>
    </aside>
  );
}

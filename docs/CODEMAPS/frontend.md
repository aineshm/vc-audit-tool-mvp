# Frontend Codemap

**Last Updated:** 2026-03-01

Next.js 16 + React 19 + Tailwind v4 web UI for VC Audit Tool.

## Entry Point

**File:** `frontend/`

- **Framework:** Next.js 16.1.6
- **Runtime:** React 19
- **Styling:** Tailwind CSS v4
- **Type system:** TypeScript
- **Node version:** 24+ (managed via nvm)

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Next.js App Router                   │
│  (src/app/ directory structure)                         │
└─────────────────────────────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        │               │               │
        v               v               v
  ┌─────────┐    ┌────────────┐  ┌──────────────┐
  │  Pages  │    │ Components │  │   Layouts    │
  │         │    │            │  │              │
  │ / (dash)│    │ RunTable   │  │ root.tsx     │
  │/research│    │ RunDetail  │  │              │
  │/value   │    │ Forms      │  │ Loading,     │
  │/reconcile   │ Charts     │  │ Error        │
  │/runs    │    │ Headers    │  │              │
  │/runs/[id]   │            │  │ Dark mode    │
  └─────────┘    └────────────┘  └──────────────┘
        │               │               │
        └───────────────┼───────────────┘
                        │
        ┌───────────────┴───────────────┐
        │                               │
        v                               v
  ┌──────────────────┐        ┌──────────────────┐
  │   Data Service   │        │   Styling        │
  │  (API Client)    │        │  (Tailwind v4)   │
  │                  │        │                  │
  │ FastAPIService   │        │ globals.css      │
  │ SupabaseService  │        │ @theme blocks    │
  │ factory:         │        │ Dark mode        │
  │ createDataService│        │ Responsive       │
  └──────────────────┘        └──────────────────┘
        │
        v
  ┌──────────────────────────────────────┐
  │   Backend (FastAPI on :8080)         │
  │   Via next.config.ts rewrites        │
  └──────────────────────────────────────┘
```

## Pages (Routes)

| Route | File | Purpose | API Calls |
|-------|------|---------|-----------|
| `/` | `src/app/page.tsx` | Dashboard — recent valuation runs | `GET /api/runs` |
| `/research` | `src/app/research/page.tsx` | Research-first form (company name only) | `POST /research` |
| `/value` | `src/app/value/page.tsx` | Manual structured valuation form | `POST /api/value` |
| `/reconcile` | `src/app/reconcile/page.tsx` | Multi-methodology reconciliation form | `POST /reconcile` |
| `/runs` | `src/app/runs/page.tsx` | Full run history table with sorting/filtering | `GET /api/runs` |
| `/runs/[id]` | `src/app/runs/[id]/page.tsx` | Detailed run view: audit trail, derivation steps, evidence | `GET /api/runs/{id}` |

## Layouts & Global Styling

| File | Purpose |
|------|---------|
| `src/app/layout.tsx` | Root layout (header, nav, dark mode toggle) |
| `src/app/globals.css` | Tailwind v4 configuration, `@theme` block, dark mode |
| `src/components/Header.tsx` | Navigation, branding, dark mode toggle |
| `src/components/Navigation.tsx` | Sidebar or top nav (site structure) |

## Components

| Component | Location | Purpose |
|-----------|----------|---------|
| `RunTable` | `src/components/RunTable.tsx` | Paginated table of recent runs |
| `RunDetail` | `src/components/RunDetail.tsx` | Full audit trail view, derivation steps, evidence table |
| `ValuationForm` | `src/components/forms/ValuationForm.tsx` | Manual `/value` form inputs |
| `ResearchForm` | `src/components/forms/ResearchForm.tsx` | Simple company name + optional description |
| `ReconcileForm` | `src/components/forms/ReconcileForm.tsx` | Company name + optional description |
| `SourceReliabilityBadge` | `src/components/SourceReliabilityBadge.tsx` | Visual confidence tier indicator |
| `DerivationStepsList` | `src/components/DerivationStepsList.tsx` | Expandable step-by-step walkthrough |
| `EvidenceTable` | `src/components/EvidenceTable.tsx` | Table of extracted evidence with source tiers |
| `LoadingSpinner` | `src/components/LoadingSpinner.tsx` | Loading state during API calls |
| `ErrorBoundary` | `src/components/ErrorBoundary.tsx` | React error boundary + user-friendly messages |

## Data Service Layer

**Files:**
- `src/lib/data-service.ts` — Interface definition
- `src/lib/fastapi-data-service.ts` — FastAPI implementation
- `src/lib/supabase-data-service.ts` — Supabase implementation (Phase 4)

**Interface:**
```typescript
interface DataService {
    // List recent runs (summary)
    listRuns(): Promise<RunSummary[]>;

    // Get full details for a single run
    getRun(runId: string): Promise<ValuationResult>;

    // Create a new valuation (research-first)
    research(request: ResearchRequest): Promise<ValuationResult>;

    // Create a new valuation (manual)
    valuate(request: ValuationRequest): Promise<ValuationResult>;

    // Create reconciled valuation (multi-method)
    reconcile(request: ReconcileRequest): Promise<ReconciliationResult>;
}
```

**Auto-selection factory:**
```typescript
// src/lib/data-service-factory.ts
export function createDataService(): DataService {
    const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
    const supabaseKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

    if (supabaseUrl && supabaseKey) {
        // Phase 4 — read from Supabase directly
        return new SupabaseDataService(supabaseUrl, supabaseKey);
    }

    // Default — use FastAPI
    return new FastAPIDataService(process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080");
}
```

## API Types

**File:** `src/types/api.ts`

```typescript
// Request types
interface ValuationRequest {
    company_name: string;
    methodology: string;
    as_of_date?: string;
    inputs: {
        [key: string]: string | number | boolean;
    };
}

interface ResearchRequest {
    company_name: string;
    as_of_date?: string;
    description_hint?: string;
    methodology?: string;
}

interface ReconcileRequest {
    company_name: string;
    as_of_date?: string;
    description_hint?: string;
}

// Response types
interface ValuationResult {
    valuation_result: {
        company_name: string;
        methodology: string;
        as_of_date: string;
        estimated_fair_value: {
            amount: number;
            currency: string;
        };
        assumptions: string[];
        derivation_steps: string[];
        confidence_indicators: {
            [key: string]: unknown;
        };
    };
    audit_metadata: {
        request_id: string;
        generated_at_utc: string;
        engine_version: string;
    };
}

interface Evidence {
    text: string;
    source_url: string;
    source_reliability_tier: "Tier 1" | "Tier 2" | "Tier 3" | "Tier 4" | "Tier 5";
    confidence_score: number;
    evidence_type: string;
    recency: string;
}

interface RunSummary {
    request_id: string;
    company_name: string;
    methodology: string;
    as_of_date: string;
    fair_value: number;
    generated_at_utc: string;
}
```

## Styling (Tailwind v4)

**File:** `src/app/globals.css`

```css
@import "tailwindcss";

@theme {
    /* Color palette */
    --color-primary: #3b82f6;      /* Blue */
    --color-secondary: #10b981;    /* Green */
    --color-danger: #ef4444;       /* Red */
    --color-warning: #f59e0b;      /* Amber */
    --color-surface: #f9fafb;      /* Light gray */
    --color-surface-dark: #1f2937; /* Dark gray */
    --color-text: #111827;         /* Almost black */
    --color-text-light: #6b7280;   /* Medium gray */
}

@variant dark (&:where(.dark, .dark *));

/* Global layout */
html {
    @apply scroll-smooth;
}

body {
    @apply bg-white dark:bg-gray-950 text-gray-900 dark:text-gray-50;
}

/* Responsive grid for run table */
.grid-runs {
    @apply grid gap-4 md:gap-6 lg:grid-cols-2 xl:grid-cols-3;
}

/* Component-specific styles */
.btn-primary {
    @apply px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 transition;
}

.badge-tier-1 {
    @apply inline-block px-2 py-1 bg-green-100 text-green-900 rounded text-sm dark:bg-green-900 dark:text-green-100;
}

.derivation-step {
    @apply pl-4 border-l-2 border-blue-500 dark:border-blue-400 py-2;
}
```

## Dark Mode

**Implementation:**
- Tailwind v4 `@variant dark` in `globals.css`
- Toggle button in `Header.tsx` → `next-themes` library
- Persisted in localStorage

**Usage:**
```tsx
<div className="bg-white dark:bg-gray-950">
    Light background → Dark background
</div>
```

## API Proxy Configuration

**File:** `next.config.ts`

```typescript
import type { NextConfig } from "next";

const config: NextConfig = {
    rewrites: async () => ({
        beforeFiles: [
            {
                source: "/api/:path*",
                destination: `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080"}/:path*`,
            },
            // Research endpoint rewrites to backend /research
            {
                source: "/research",
                destination: "/api/research",
            },
            // Reconcile endpoint rewrites to backend /reconcile
            {
                source: "/reconcile",
                destination: "/api/reconcile",
            },
        ],
    }),
};

export default config;
```

## Environment Variables

**File:** `frontend/.env.local.example`

```bash
# Backend API URL (defaults to http://localhost:8080)
NEXT_PUBLIC_API_URL=http://localhost:8080

# Supabase integration (Phase 4 — set both to enable direct DB reads)
NEXT_PUBLIC_SUPABASE_URL=https://drykfbevdfyivyhnkyfc.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

**Setup:**
```bash
cd frontend
cp .env.local.example .env.local
# Edit .env.local with your values
npm run dev
```

## Build & Deployment

```bash
# Development server (with hot reload)
source ~/.nvm/nvm.sh && nvm use 24 --silent
npm run dev        # http://localhost:3000

# Production build
npm run build
npm start          # Serve production build

# Docker deployment (example)
docker build -t vc-audit-frontend .
docker run -p 3000:3000 -e NEXT_PUBLIC_API_URL=... vc-audit-frontend
```

## Testing

**Unit Tests:** `src/**/*.test.ts`
```bash
npm test           # Jest test runner
npm test -- --watch
```

**E2E Tests:** `e2e/**/*.e2e.ts` (Playwright recommended)
```bash
npx playwright test
npx playwright codegen http://localhost:3000  # Record interactions
```

## Performance Optimization

1. **Image optimization** — Next.js `<Image>` component
2. **Code splitting** — Automatic per-route
3. **Lazy loading** — `React.lazy()` for heavy components
4. **API caching** — React Query / SWR for request deduplication
5. **CSS pruning** — Tailwind automatically removes unused classes

## Dependencies

Key packages:
- `react` 19
- `next` 16
- `tailwindcss` 4
- `typescript` (for type safety)
- `@supabase/supabase-js` (optional, for Phase 4)

See `frontend/package.json` for full list.

## Related Codemaps

- **[backend.md](./backend.md)** — FastAPI server, data sources
- **[storage.md](./storage.md)** — Supabase integration, data service layer

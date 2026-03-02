import type {
  HealthStatus,
  RunSummary,
  ValuationEnvelope,
  ReconciledEnvelope,
  ResearchRequest,
  ReconcileRequest,
  ValuationRequest,
} from "@/types/api";

export interface DataService {
  getHealth(): Promise<HealthStatus>;
  listRuns(): Promise<RunSummary[]>;
  getRunById(id: string): Promise<ValuationEnvelope | null>;
  runResearch(params: ResearchRequest): Promise<ValuationEnvelope>;
  runReconcile(params: ReconcileRequest): Promise<ReconciledEnvelope>;
  runManualValuation(params: ValuationRequest): Promise<ValuationEnvelope>;
}

export class FastAPIDataService implements DataService {
  private baseUrl: string;

  constructor() {
    this.baseUrl =
      typeof window === "undefined"
        ? (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080")
        : "";
  }

  private async fetchJson<T>(path: string, options?: RequestInit): Promise<T> {
    const requestId = crypto.randomUUID();
    const res = await fetch(`${this.baseUrl}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        "X-Request-ID": requestId,
        ...options?.headers,
      },
    });
    if (!res.ok) {
      const errorData = await res
        .json()
        .catch(() => ({ error: res.statusText }));
      throw new Error(
        (errorData as { error?: string }).error ?? `HTTP ${res.status}`
      );
    }
    return res.json() as Promise<T>;
  }

  async getHealth(): Promise<HealthStatus> {
    return this.fetchJson<HealthStatus>("/health");
  }

  async listRuns(): Promise<RunSummary[]> {
    return this.fetchJson<RunSummary[]>("/api/runs");
  }

  async getRunById(id: string): Promise<ValuationEnvelope | null> {
    try {
      return await this.fetchJson<ValuationEnvelope>(`/api/runs/${id}`);
    } catch {
      return null;
    }
  }

  async runResearch(params: ResearchRequest): Promise<ValuationEnvelope> {
    return this.fetchJson<ValuationEnvelope>("/api/research", {
      method: "POST",
      body: JSON.stringify(params),
    });
  }

  async runReconcile(params: ReconcileRequest): Promise<ReconciledEnvelope> {
    return this.fetchJson<ReconciledEnvelope>("/api/reconcile", {
      method: "POST",
      body: JSON.stringify(params),
    });
  }

  async runManualValuation(params: ValuationRequest): Promise<ValuationEnvelope> {
    return this.fetchJson<ValuationEnvelope>("/api/value", {
      method: "POST",
      body: JSON.stringify(params),
    });
  }
}

export function createDataService(): DataService {
  if (
    process.env.NEXT_PUBLIC_SUPABASE_URL &&
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY
  ) {
    // Lazy import avoids bundling supabase-js when env vars are absent.
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { SupabaseDataService } = require("./supabase-data-service") as {
      SupabaseDataService: new () => DataService;
    };
    return new SupabaseDataService();
  }
  return new FastAPIDataService();
}

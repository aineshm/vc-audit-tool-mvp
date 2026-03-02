/**
 * SupabaseDataService — read-only run queries bypass FastAPI and hit
 * Supabase directly.  All write operations (research, reconcile, value)
 * and health checks delegate to FastAPIDataService.
 */
import type {
  HealthStatus,
  RunSummary,
  ValuationEnvelope,
  ReconciledEnvelope,
  ResearchRequest,
  ReconcileRequest,
  ValuationRequest,
} from "@/types/api";
import type { DataService } from "./data-service";
import { FastAPIDataService } from "./data-service";
import { supabase } from "./supabase";

export class SupabaseDataService implements DataService {
  private readonly _api = new FastAPIDataService();

  // ── Reads (go directly to Supabase) ──

  async listRuns(): Promise<RunSummary[]> {
    const { data, error } = await supabase
      .from("valuation_runs")
      .select(
        "request_id,company_name,methodology,as_of_date,fair_value,generated_at_utc"
      )
      .order("generated_at_utc", { ascending: false })
      .limit(50);

    if (error) throw new Error(`Supabase listRuns failed: ${error.message}`);
    return (data ?? []) as RunSummary[];
  }

  async getRunById(id: string): Promise<ValuationEnvelope | null> {
    const { data, error } = await supabase
      .from("valuation_runs")
      .select("payload")
      .eq("request_id", id)
      .maybeSingle();

    if (error) throw new Error(`Supabase getRunById failed: ${error.message}`);
    if (!data) return null;
    return JSON.parse(data.payload as string) as ValuationEnvelope;
  }

  // ── Writes and health (delegate to FastAPI) ──

  async getHealth(): Promise<HealthStatus> {
    return this._api.getHealth();
  }

  async runResearch(params: ResearchRequest): Promise<ValuationEnvelope> {
    return this._api.runResearch(params);
  }

  async runReconcile(params: ReconcileRequest): Promise<ReconciledEnvelope> {
    return this._api.runReconcile(params);
  }

  async runManualValuation(params: ValuationRequest): Promise<ValuationEnvelope> {
    return this._api.runManualValuation(params);
  }
}

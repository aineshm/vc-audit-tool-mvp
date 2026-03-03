// API response types for vc-audit-tool FastAPI backend

export interface HealthStatus {
  status: string;
  version: string;
  store: string;
  llm_provider: string | null;
  pinecone_index: string | null;
  request_id: string;
}

export interface CitationItem {
  label: string;
  detail: string;
  dataset_version?: string;
  resolved_data_points?: Record<string, unknown>;
}

export interface ConfidenceIndicators {
  overall: number;
  data_quality: number;
  methodology_fit: number;
  [key: string]: number | string;
}

export interface ValuationResult {
  fair_value?: number;
  estimated_fair_value?: { amount: number; currency: string };
  methodology: string;
  confidence_indicators: ConfidenceIndicators;
  derivation_steps: string[];
  assumptions: string[];
  citations: CitationItem[];
  inputs_used: Record<string, unknown>;
}

export interface AuditMetadata {
  request_id: string;
  generated_at_utc: string;
  engine_version: string;
  as_of_date: string;
}

export interface ValuationEvidence {
  amount_usd: number;
  evidence_type: string;
  confidence: number;
  source_reliability_tier: string | null;
  date_mentioned: string | null;
  source_title: string | null;
  source_snippet: string;
}

export interface EvidencePackage {
  company_name: string;
  evidence_count: number;
  consensus_valuation: number | null;
  consensus_strength: "STRONG" | "MODERATE" | "WEAK" | "NONE";
  recommended_methodology: string;
  best_revenue: number | null;
  best_round_date: string | null;
  evidence: ValuationEvidence[];
  extraction_timestamp: string;
}

export interface CompanyProfile {
  name: string;
  stage: string;
  sector: string;
  age_years: number | null;
  has_revenue: boolean;
  estimated_arr: number | null;
  last_round_age_months: number | null;
  last_round_amount: number | null;
  last_post_money: number | null;
  sic_code: string | null;
  headcount: number | null;
  government_contracts_usd: number | null;
  profile_summary: string;
  sources_used: string[];
}

export interface MethodologyResult {
  methodology: string;
  weight: number;
  point_estimate: number | null;
  rationale: string;
  data_requirements_met: boolean;
}

export interface ConcludedValue {
  point_estimate: number;
  range_low: number;
  range_high: number;
  currency: string;
  as_of_date: string;
}

export interface ReconciliationSummary {
  methodology_weights: MethodologyResult[];
  divergence_flag: boolean;
  divergence_note: string | null;
  reconciliation_rationale: string;
  selector_version: string;
}

export interface ResearchMetadata {
  company_name: string;
  sources_consulted: string[];
  extracted_facts: Record<string, unknown>;
  llm_model_version: string | null;
  evidence_package?: EvidencePackage;
  web_facts?: Record<string, unknown>;
}

export interface ValuationEnvelope {
  valuation_result?: ValuationResult;
  audit_metadata?: AuditMetadata;
  research_metadata?: ResearchMetadata;
  error?: string;
  missing_fields?: string[];
  status?: string;
}

export interface ReconciledEnvelope {
  concluded_value?: ConcludedValue;
  reconciliation?: ReconciliationSummary;
  methodology_results?: Record<string, Record<string, unknown>>;
  company_profile?: CompanyProfile;
  audit_metadata?: AuditMetadata;
  research_metadata?: ResearchMetadata;
  error?: string;
}

export interface RunSummary {
  request_id: string;
  company_name: string;
  methodology: string;
  as_of_date: string;
  fair_value: number | null;
  generated_at_utc: string;
}

// Request shapes
export interface ResearchRequest {
  company_name: string;
  as_of_date?: string;
  methodology?: string;
  description_hint?: string;
}

export interface ReconcileRequest {
  company_name: string;
  as_of_date?: string;
  description_hint?: string;
}

export interface ValuationRequest {
  company_name: string;
  methodology: string;
  as_of_date: string;
  inputs: Record<string, unknown>;
}

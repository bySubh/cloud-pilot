export type RunStatus = "queued" | "running" | "success" | "failed";

export interface ResourceSpec {
  resource_type: string;
  purpose: string;
  reason: string;
  name_hint?: string | null;
}

export interface ComplianceViolation {
  rule_id: string;
  severity: string;
  resource_address: string;
  description: string;
  remediation_hint: string;
}

export type PolicyEngineStatus =
  | "checkov_ran"
  | "checkov_unavailable_fallback_used"
  | "checkov_crashed_fallback_used"
  | "fallback_crashed";

export interface PolicyResult {
  source: "checkov" | "fallback" | string;
  status: PolicyEngineStatus;
  passed_checks: number;
  failed_checks: number;
  skipped_checks: number;
  violations: ComplianceViolation[];
  checkov_available: boolean;
  crashed: boolean;
  raw_error?: string | null;
  status_message?: string;
}

export type TerraformExecutionMode = "real" | "mock" | "unavailable";

export interface TerraformCommandResult {
  command: string[];
  return_code: number;
  stdout: string;
  stderr: string;
  duration_seconds: number;
  workspace: string;
  timed_out: boolean;
  mode: TerraformExecutionMode;
}

export interface TerraformPlanOutcome {
  init_result?: TerraformCommandResult | null;
  plan_result?: TerraformCommandResult | null;
  success: boolean;
  mode: TerraformExecutionMode;
}

export type GenerationMethod = "vertex_llm" | "mock_llm" | "deterministic_fallback" | "unknown";

export interface HealAttemptRecord {
  attempt: number;
  terraform_status: "success" | "failed";
  violations: number;
  errors: string[];
}

export interface RunResult {
  run_id: string;
  status: RunStatus;
  heal_attempts: number;
  resources: ResourceSpec[];
  terraform_files: Record<string, string>;
  terraform_generation_method: GenerationMethod;
  terraform_generation_notes: string[];
  policy_results: Partial<PolicyResult>;
  terraform_plan: Partial<TerraformPlanOutcome>;
  heal_history: HealAttemptRecord[];
  workspace: string;
  errors: string[];
  request: string;
  constraints: string;
  created_at: string;
  updated_at: string;
  current_node: string;
}

export interface RunCreateRequest {
  request: string;
  constraints?: string;
  application_source?: string | null;
}

export interface RunCreateResponse {
  run_id: string;
  status: RunStatus;
}

export interface LogEvent {
  run_id: string;
  node: string;
  status: string;
  message: string;
  timestamp: string;
  duration_ms?: number | null;
}

export type PipelineStageKey = "architect" | "iac_developer" | "policy_linter" | "terraform_runner" | "result";

export type StageState = "pending" | "running" | "success" | "failed" | "healing";

import type { RunCreateRequest, RunCreateResponse, RunResult, LogEvent } from "../types";

const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      // ignore body parse failure, fall back to statusText
    }
    throw new ApiError(detail, response.status);
  }

  return (await response.json()) as T;
}

export const api = {
  health(): Promise<{ status: string; service: string }> {
    return request("/health");
  },

  createRun(payload: RunCreateRequest): Promise<RunCreateResponse> {
    return request("/runs", { method: "POST", body: JSON.stringify(payload) });
  },

  listRuns(): Promise<RunResult[]> {
    return request("/runs");
  },

  getRun(runId: string): Promise<RunResult> {
    return request(`/runs/${runId}`);
  },

  getRunStatus(runId: string): Promise<{ run_id: string; status: string; current_node: string; heal_attempts: number }> {
    return request(`/runs/${runId}/status`);
  },

  getRunLogs(runId: string): Promise<LogEvent[]> {
    return request(`/runs/${runId}/logs`);
  },
};

export { ApiError, API_BASE_URL };

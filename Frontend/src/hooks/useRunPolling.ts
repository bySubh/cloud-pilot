import { useEffect, useRef, useState } from "react";
import { api } from "../services/api";
import type { RunResult } from "../types";

const TERMINAL_STATUSES = new Set(["success", "failed"]);
const POLL_INTERVAL_MS = 1500;

export function useRunPolling(runId: string | undefined) {
  const [run, setRun] = useState<RunResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const intervalRef = useRef<number | null>(null);

  useEffect(() => {
    if (!runId) return;

    let cancelled = false;

    const poll = async () => {
      try {
        const result = await api.getRun(runId);
        if (cancelled) return;
        setRun(result);
        setError(null);
        if (TERMINAL_STATUSES.has(result.status) && intervalRef.current) {
          window.clearInterval(intervalRef.current);
          intervalRef.current = null;
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to fetch run");
      }
    };

    poll();
    intervalRef.current = window.setInterval(poll, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      if (intervalRef.current) window.clearInterval(intervalRef.current);
    };
  }, [runId]);

  return { run, error };
}

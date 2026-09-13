import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import NewRunForm from "../components/NewRunForm";
import RunList from "../components/RunList";
import { api } from "../services/api";
import type { RunResult } from "../types";

export default function Dashboard() {
  const [runs, setRuns] = useState<RunResult[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const navigate = useNavigate();

  const loadRuns = async () => {
    try {
      const result = await api.listRuns();
      setRuns(result);
      setLoadError(null);
    } catch (err) {
      setLoadError("Could not reach the Cloud Pilot backend. Is it running on VITE_API_BASE_URL?");
    }
  };

  useEffect(() => {
    loadRuns();
    const interval = setInterval(loadRuns, 5000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="max-w-4xl mx-auto px-4 py-10 space-y-8">
      <header>
        <h1 className="text-2xl font-bold">Cloud Pilot</h1>
        <p className="text-slate-400 text-sm mt-1">
          Describe the infrastructure you need in plain English. Cloud Pilot architects it, generates secure
          Terraform, validates it, and heals it automatically.
        </p>
      </header>

      <NewRunForm onRunCreated={(runId) => navigate(`/runs/${runId}`)} />

      <section>
        <h2 className="text-lg font-semibold mb-3">Recent Runs</h2>
        {loadError && <p className="text-rose-400 text-sm mb-3">{loadError}</p>}
        <RunList runs={runs} />
      </section>
    </div>
  );
}

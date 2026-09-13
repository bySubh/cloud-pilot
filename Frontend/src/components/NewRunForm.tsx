import { useState, type FormEvent } from "react";
import { api, ApiError } from "../services/api";

interface Props {
  onRunCreated: (runId: string) => void;
}

export default function NewRunForm({ onRunCreated }: Props) {
  const [request, setRequest] = useState("");
  const [constraints, setConstraints] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (request.trim().length < 3) {
      setError("Please describe the infrastructure you need.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const response = await api.createRun({ request, constraints });
      onRunCreated(response.run_id);
      setRequest("");
      setConstraints("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start run. Is the backend running?");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="bg-pilot-panel border border-pilot-border rounded-xl p-6 space-y-4">
      <h2 className="text-lg font-semibold">New Infrastructure Run</h2>

      <div>
        <label className="block text-sm text-slate-400 mb-1">Infrastructure Request</label>
        <textarea
          value={request}
          onChange={(e) => setRequest(e.target.value)}
          placeholder="Create infrastructure for a web application that needs PostgreSQL, object storage, and a compute instance."
          className="w-full rounded-lg bg-pilot-bg border border-pilot-border px-3 py-2 text-sm min-h-[90px] focus:outline-none focus:border-pilot-accent"
        />
      </div>

      <div>
        <label className="block text-sm text-slate-400 mb-1">Constraints (optional)</label>
        <textarea
          value={constraints}
          onChange={(e) => setConstraints(e.target.value)}
          placeholder="Use secure defaults, no public IPs, region us-central1..."
          className="w-full rounded-lg bg-pilot-bg border border-pilot-border px-3 py-2 text-sm min-h-[60px] focus:outline-none focus:border-pilot-accent"
        />
      </div>

      {error && <p className="text-rose-400 text-sm">{error}</p>}

      <button
        type="submit"
        disabled={submitting}
        className="w-full sm:w-auto px-5 py-2.5 rounded-lg bg-pilot-accent text-slate-900 font-semibold hover:opacity-90 disabled:opacity-50 transition"
      >
        {submitting ? "Starting..." : "Generate Infrastructure"}
      </button>
    </form>
  );
}

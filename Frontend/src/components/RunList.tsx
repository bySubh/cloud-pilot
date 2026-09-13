import { Link } from "react-router-dom";
import type { RunResult } from "../types";

const STATUS_STYLES: Record<string, string> = {
  queued: "text-slate-400",
  running: "text-sky-400",
  success: "text-emerald-400",
  failed: "text-rose-400",
};

export default function RunList({ runs }: { runs: RunResult[] }) {
  if (runs.length === 0) {
    return <p className="text-slate-500 text-sm">No runs yet. Start one above.</p>;
  }

  return (
    <div className="divide-y divide-pilot-border">
      {runs.map((run) => (
        <Link
          key={run.run_id}
          to={`/runs/${run.run_id}`}
          className="flex items-center justify-between py-3 px-1 hover:bg-pilot-panel/60 rounded transition"
        >
          <div className="min-w-0">
            <p className="text-sm truncate">{run.request}</p>
            <p className="text-xs text-slate-500 font-mono">{run.run_id}</p>
          </div>
          <span className={`text-xs font-semibold uppercase ml-4 shrink-0 ${STATUS_STYLES[run.status] || ""}`}>
            {run.status}
          </span>
        </Link>
      ))}
    </div>
  );
}

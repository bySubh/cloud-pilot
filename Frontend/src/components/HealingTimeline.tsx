import type { HealAttemptRecord } from "../types";

export default function HealingTimeline({ history }: { history: HealAttemptRecord[] }) {
  if (!history || history.length === 0) {
    return <p className="text-slate-500 text-sm">No validation attempts recorded yet.</p>;
  }

  return (
    <ol className="space-y-3">
      {history.map((entry) => (
        <li
          key={entry.attempt}
          className={`rounded-lg border px-4 py-3 ${
            entry.terraform_status === "success"
              ? "border-emerald-700 bg-emerald-950/20"
              : "border-amber-700 bg-amber-950/20"
          }`}
        >
          <div className="flex items-center justify-between">
            <span className="font-semibold">Attempt {entry.attempt}</span>
            <span
              className={
                entry.terraform_status === "success" ? "text-emerald-400 text-sm" : "text-amber-400 text-sm"
              }
            >
              {entry.terraform_status === "success" ? "Success" : "Needed healing"}
            </span>
          </div>
          <div className="text-xs text-slate-400 mt-1">Policy violations: {entry.violations}</div>
          {entry.errors.length > 0 && (
            <pre className="text-xs text-rose-300 mt-2 whitespace-pre-wrap max-h-32 overflow-auto">
              {entry.errors.join("\n")}
            </pre>
          )}
        </li>
      ))}
    </ol>
  );
}

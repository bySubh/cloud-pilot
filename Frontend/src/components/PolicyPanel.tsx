import type { PolicyResult } from "../types";

const SEVERITY_COLORS: Record<string, string> = {
  CRITICAL: "text-rose-400",
  HIGH: "text-orange-400",
  MEDIUM: "text-amber-400",
  LOW: "text-sky-400",
  UNKNOWN: "text-slate-400",
};

const STATUS_BADGE: Record<string, { label: string; className: string }> = {
  checkov_ran: { label: "Checkov ran", className: "bg-emerald-900/40 border-emerald-700 text-emerald-300" },
  checkov_unavailable_fallback_used: {
    label: "Checkov not installed - fallback used",
    className: "bg-amber-900/40 border-amber-700 text-amber-300",
  },
  checkov_crashed_fallback_used: {
    label: "Checkov crashed - fallback used",
    className: "bg-amber-900/40 border-amber-700 text-amber-300",
  },
  fallback_crashed: { label: "Fallback validator crashed", className: "bg-rose-900/40 border-rose-700 text-rose-300" },
};

export default function PolicyPanel({ policy }: { policy: Partial<PolicyResult> | undefined }) {
  if (!policy || Object.keys(policy).length === 0) {
    return <p className="text-slate-500 text-sm">Policy checks have not run yet.</p>;
  }

  const badge = policy.status ? STATUS_BADGE[policy.status] : undefined;

  return (
    <div className="space-y-4">
      {badge && (
        <div className={`inline-block px-3 py-1 rounded border text-xs font-semibold ${badge.className}`}>
          {badge.label}
        </div>
      )}

      {policy.status_message && <p className="text-sm text-slate-300">{policy.status_message}</p>}

      <div className="flex flex-wrap gap-4 text-sm">
        <span className="px-3 py-1 rounded bg-pilot-panel border border-pilot-border">
          Source: <span className="font-semibold">{policy.source}</span>
        </span>
        <span className="px-3 py-1 rounded bg-emerald-900/40 border border-emerald-700 text-emerald-300">
          Passed: {policy.passed_checks ?? 0}
        </span>
        <span className="px-3 py-1 rounded bg-rose-900/40 border border-rose-700 text-rose-300">
          Failed: {policy.failed_checks ?? 0}
        </span>
        <span className="px-3 py-1 rounded bg-slate-800 border border-slate-700 text-slate-300">
          Skipped: {policy.skipped_checks ?? 0}
        </span>
      </div>

      {policy.raw_error && (
        <p className="text-xs text-amber-400 font-mono bg-amber-950/30 border border-amber-800 rounded p-2">
          {policy.raw_error}
        </p>
      )}

      {policy.violations && policy.violations.length > 0 && (
        <table className="w-full text-xs border-collapse">
          <thead>
            <tr className="text-left text-slate-400 border-b border-pilot-border">
              <th className="py-2 pr-4">Rule</th>
              <th className="py-2 pr-4">Severity</th>
              <th className="py-2 pr-4">Resource</th>
              <th className="py-2 pr-4">Description</th>
              <th className="py-2">Remediation</th>
            </tr>
          </thead>
          <tbody>
            {policy.violations.map((v) => (
              <tr key={`${v.rule_id}-${v.resource_address}`} className="border-b border-pilot-border/50">
                <td className="py-2 pr-4 font-mono">{v.rule_id}</td>
                <td className={`py-2 pr-4 font-semibold ${SEVERITY_COLORS[v.severity] || "text-slate-300"}`}>
                  {v.severity}
                </td>
                <td className="py-2 pr-4 font-mono">{v.resource_address}</td>
                <td className="py-2 pr-4">{v.description}</td>
                <td className="py-2 text-slate-400">{v.remediation_hint}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

import type { TerraformPlanOutcome } from "../types";

const MODE_BANNER: Record<string, { label: string; className: string }> = {
  real: { label: "Real terraform execution", className: "bg-emerald-900/40 border-emerald-700 text-emerald-300" },
  mock: {
    label: "MOCK validation - terraform binary not installed (static checks only, not a real GCP plan)",
    className: "bg-amber-900/40 border-amber-700 text-amber-300",
  },
  unavailable: {
    label: "Terraform validation unavailable - not executed",
    className: "bg-rose-900/40 border-rose-700 text-rose-300",
  },
};

export default function TerraformPlanPanel({ plan }: { plan: Partial<TerraformPlanOutcome> | undefined }) {
  if (!plan || (!plan.init_result && !plan.plan_result)) {
    return <p className="text-slate-500 text-sm">Terraform has not run yet.</p>;
  }

  const banner = plan.mode ? MODE_BANNER[plan.mode] : undefined;

  return (
    <div className="space-y-4">
      {banner && (
        <div className={`px-3 py-2 rounded border text-xs font-semibold ${banner.className}`}>{banner.label}</div>
      )}
      <div className="text-sm">
        Overall:{" "}
        <span className={plan.success ? "text-emerald-400 font-semibold" : "text-rose-400 font-semibold"}>
          {plan.success ? "Success" : "Failed"}
        </span>
      </div>
      {plan.init_result && (
        <div>
          <h4 className="text-xs uppercase tracking-wide text-slate-400 mb-1">terraform init</h4>
          <CommandBlock result={plan.init_result} />
        </div>
      )}
      {plan.plan_result && (
        <div>
          <h4 className="text-xs uppercase tracking-wide text-slate-400 mb-1">terraform plan</h4>
          <CommandBlock result={plan.plan_result} />
        </div>
      )}
    </div>
  );
}

function CommandBlock({ result }: { result: any }) {
  const ok = result.return_code === 0 && !result.timed_out;
  return (
    <div className={`rounded border p-3 text-xs font-mono ${ok ? "border-emerald-800 bg-emerald-950/20" : "border-rose-800 bg-rose-950/20"}`}>
      <div className="mb-1 text-slate-400">
        return_code={result.return_code} duration={result.duration_seconds?.toFixed(2)}s
        {result.timed_out && <span className="text-amber-400 ml-2">(timed out)</span>}
      </div>
      {result.stdout && <pre className="whitespace-pre-wrap max-h-48 overflow-auto">{result.stdout}</pre>}
      {result.stderr && (
        <pre className="whitespace-pre-wrap max-h-48 overflow-auto text-rose-300 mt-1">{result.stderr}</pre>
      )}
    </div>
  );
}

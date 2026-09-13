import type { RunResult, StageState } from "../types";

interface Stage {
  key: string;
  label: string;
}

const STAGES: Stage[] = [
  { key: "architect", label: "Architect" },
  { key: "iac_developer", label: "IaC Developer" },
  { key: "policy_linter", label: "Policy Linter" },
  { key: "terraform_runner", label: "Terraform Plan" },
  { key: "result", label: "Result" },
];

function stageState(run: RunResult | null, stageKey: string): StageState {
  if (!run) return "pending";

  if (stageKey === "result") {
    if (run.status === "success") return "success";
    if (run.status === "failed") return "failed";
    return "pending";
  }

  if (run.status === "queued") return "pending";

  const order = ["architect", "iac_developer", "policy_linter", "terraform_runner"];
  const currentIndex = order.indexOf(run.current_node);
  const stageIndex = order.indexOf(stageKey);

  if (run.status === "running") {
    if (run.heal_attempts > 0 && stageIndex <= order.indexOf("terraform_runner") && stageIndex >= order.indexOf("iac_developer")) {
      return "healing";
    }
    if (stageIndex < currentIndex) return "success";
    if (stageIndex === currentIndex) return "running";
    return "pending";
  }

  // terminal states: everything up to terraform_runner is considered done
  if (run.status === "success") return "success";
  if (run.status === "failed") {
    return stageIndex <= order.indexOf("terraform_runner") ? "success" : "pending";
  }
  return "pending";
}

const STATE_STYLES: Record<StageState, string> = {
  pending: "bg-slate-800 text-slate-400 border-slate-700",
  running: "bg-sky-900/60 text-sky-300 border-sky-500 animate-pulse",
  success: "bg-emerald-900/50 text-emerald-300 border-emerald-500",
  failed: "bg-rose-900/50 text-rose-300 border-rose-500",
  healing: "bg-amber-900/50 text-amber-300 border-amber-500",
};

const STATE_LABELS: Record<StageState, string> = {
  pending: "Pending",
  running: "Running",
  success: "Success",
  failed: "Failed",
  healing: "Healing",
};

export default function PipelineStages({ run }: { run: RunResult | null }) {
  return (
    <div className="flex flex-col sm:flex-row gap-3 sm:items-center">
      {STAGES.map((stage, idx) => {
        const state = stageState(run, stage.key);
        return (
          <div key={stage.key} className="flex items-center gap-3 flex-1">
            <div className={`flex-1 rounded-lg border px-4 py-3 text-center ${STATE_STYLES[state]}`}>
              <div className="text-sm font-semibold">{stage.label}</div>
              <div className="text-xs mt-1 opacity-80">{STATE_LABELS[state]}</div>
            </div>
            {idx < STAGES.length - 1 && <span className="hidden sm:block text-slate-600">&rarr;</span>}
          </div>
        );
      })}
    </div>
  );
}

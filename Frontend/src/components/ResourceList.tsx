import type { ResourceSpec } from "../types";

export default function ResourceList({ resources }: { resources: ResourceSpec[] }) {
  if (!resources || resources.length === 0) {
    return <p className="text-slate-500 text-sm">The Architect has not produced resources yet.</p>;
  }

  return (
    <ul className="space-y-3">
      {resources.map((r, idx) => (
        <li key={`${r.resource_type}-${idx}`} className="rounded-lg border border-pilot-border p-3">
          <div className="flex items-center justify-between">
            <span className="font-mono text-sky-300 text-sm">{r.resource_type}</span>
            {r.name_hint && <span className="text-xs text-slate-500">{r.name_hint}</span>}
          </div>
          <p className="text-sm mt-1">{r.purpose}</p>
          <p className="text-xs text-slate-500 mt-1">{r.reason}</p>
        </li>
      ))}
    </ul>
  );
}

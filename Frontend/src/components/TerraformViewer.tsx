import { useState } from "react";

const FILE_ORDER = ["main.tf", "variables.tf", "providers.tf", "terraform.tfvars.example"];

export default function TerraformViewer({ files }: { files: Record<string, string> }) {
  const availableFiles = FILE_ORDER.filter((name) => files[name] !== undefined);
  const [active, setActive] = useState<string>(availableFiles[0] || "");

  if (availableFiles.length === 0) {
    return <p className="text-slate-500 text-sm">No Terraform files generated yet.</p>;
  }

  return (
    <div className="border border-pilot-border rounded-lg overflow-hidden">
      <div className="flex border-b border-pilot-border bg-pilot-panel">
        {availableFiles.map((name) => (
          <button
            key={name}
            onClick={() => setActive(name)}
            className={`px-4 py-2 text-sm font-mono border-r border-pilot-border ${
              active === name ? "bg-pilot-bg text-sky-300" : "text-slate-400 hover:text-slate-200"
            }`}
          >
            {name}
          </button>
        ))}
      </div>
      <pre className="p-4 text-xs overflow-auto max-h-96 bg-pilot-bg text-slate-200 font-mono leading-relaxed">
        {files[active] || "// empty"}
      </pre>
    </div>
  );
}

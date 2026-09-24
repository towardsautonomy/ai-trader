"use client";

import { Panel } from "@/components/ui/Panel";
import { QueryBody } from "@/components/ui/States";
import { useStatus } from "@/hooks/queries";

export function ModelsPanel({ className = "" }: { className?: string }) {
  const status = useStatus();
  return (
    <Panel title="AGENTS / MODELS" className={className} right={status.data ? <span>provider: {status.data.llm}</span> : null}>
      <QueryBody query={status} what="models">
        {(s) =>
          !s.models?.length ? (
            <div className="p-3 text-sm text-dim">this backend does not report models</div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="label text-left">
                  <th className="px-3 py-1 font-normal">agent</th>
                  <th className="px-2 py-1 font-normal">model</th>
                  <th className="px-2 py-1 font-normal">where</th>
                  <th className="px-3 py-1 font-normal">thinking</th>
                </tr>
              </thead>
              <tbody>
                {s.models.map((m) => (
                  <tr key={m.agent} className="border-t border-line/60">
                    <td className="px-3 py-1 text-fg">{m.agent}</td>
                    <td className={`break-all px-2 py-1 ${m.resolved ? "text-phos" : "font-bold text-danger"}`} title={m.resolved ? "" : "no provider configured for this model"}>
                      {m.model}
                      {m.resolved ? "" : " (NO PROVIDER)"}
                    </td>
                    <td className="px-2 py-1 text-dim">{m.provider === "local" ? "this machine" : "OpenRouter"}</td>
                    <td className={`px-3 py-1 ${m.thinking ? "text-amber" : "text-faint"}`}>{m.thinking || "off"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        }
      </QueryBody>
    </Panel>
  );
}

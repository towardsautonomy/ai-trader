"use client";

import { useParams } from "next/navigation";
import { PageHeader } from "@/components/PageHeader";
import { DecisionTraceView } from "@/components/trace/DecisionTraceView";
import { Panel } from "@/components/ui/Panel";
import { QueryBody } from "@/components/ui/States";
import { useDecisionTrace } from "@/hooks/queries";

export default function DecisionPage() {
  const { id } = useParams<{ id: string }>();
  const trace = useDecisionTrace(id);
  return (
    <div>
      <PageHeader title={`decision trace ${id}`} />
      <Panel title="REASONING CHAIN">
        <QueryBody query={trace} what={`decision ${id}`}>
          {(t) => (
            <div className="p-3">
              <DecisionTraceView trace={t} />
            </div>
          )}
        </QueryBody>
      </Panel>
    </div>
  );
}

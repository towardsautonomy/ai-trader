"use client";

import { useParams } from "next/navigation";
import { CloseControl } from "@/components/CloseControl";
import { PageHeader } from "@/components/PageHeader";
import { PositionTraceView } from "@/components/trace/PositionTraceView";
import { QueryBody } from "@/components/ui/States";
import { usePositionTrace } from "@/hooks/queries";

export default function PositionPage() {
  const { id } = useParams<{ id: string }>();
  const trace = usePositionTrace(id);
  const position = trace.data?.position;
  return (
    <div>
      <PageHeader title={`position trace ${id}`} right={position && position.status !== "closed" ? <CloseControl p={position} /> : null} />
      <div className={trace.data ? "" : "border border-line bg-panel"}>
        <QueryBody query={trace} what={`position ${id}`}>
          {(t) => <PositionTraceView trace={t} />}
        </QueryBody>
      </div>
    </div>
  );
}

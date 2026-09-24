import type { ReactNode } from "react";

export type StepTone = "ok" | "warn" | "fail" | "info" | "skipped";

const MARKER: Record<StepTone, string> = {
  ok: "bg-phos border-phos",
  warn: "bg-amber border-amber",
  fail: "bg-danger border-danger",
  info: "bg-cyan border-cyan",
  skipped: "bg-bg border-faint",
};

const TITLE: Record<StepTone, string> = {
  ok: "text-phos",
  warn: "text-amber",
  fail: "text-danger",
  info: "text-cyan",
  skipped: "text-faint",
};

interface StepProps {
  n: number;
  title: string;
  tone?: StepTone;
  /** Short verdict shown beside the title, e.g. "APPROVED" or "NOT REACHED". */
  tag?: ReactNode;
  children: ReactNode;
}

/** One stage of the reasoning chain on the vertical timeline. */
export function Step({ n, title, tone = "ok", tag, children }: StepProps) {
  return (
    <li className="relative border-l border-line-hi pb-5 pl-6 last:pb-1">
      <span className={`absolute -left-[5px] top-1 h-[9px] w-[9px] border ${MARKER[tone]}`} aria-hidden />
      <div className="mb-1.5 flex flex-wrap items-baseline gap-3">
        <h3 className={`text-sm font-bold uppercase tracking-[0.14em] ${TITLE[tone]}`}>
          <span className="text-faint">{`${String(n).padStart(2, "0")} //`}</span> {title}
        </h3>
        {tag ? <span className="text-xs">{tag}</span> : null}
      </div>
      <div className="min-w-0">{children}</div>
    </li>
  );
}

export function NotReached({ why }: { why: string }) {
  return <div className="text-sm text-faint">not reached: {why}</div>;
}

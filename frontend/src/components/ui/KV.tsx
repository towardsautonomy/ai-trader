import type { ReactNode } from "react";

/** One labelled value in a dense definition grid. */
export function KV({ k, children, className = "" }: { k: string; children: ReactNode; className?: string }) {
  return (
    <div className={`min-w-0 ${className}`}>
      <div className="label">{k}</div>
      <div className="break-words text-sm text-fg">{children}</div>
    </div>
  );
}

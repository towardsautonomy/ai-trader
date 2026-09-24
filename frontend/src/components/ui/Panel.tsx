import type { ReactNode } from "react";

interface PanelProps {
  title: string;
  /** Right-aligned controls or counters in the title bar. */
  right?: ReactNode;
  className?: string;
  bodyClassName?: string;
  children: ReactNode;
}

export function Panel({ title, right, className = "", bodyClassName = "", children }: PanelProps) {
  return (
    <section className={`flex min-h-0 min-w-0 flex-col border border-line bg-panel ${className}`}>
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-line px-2 py-1">
        <h2 className="whitespace-nowrap text-xs uppercase tracking-[0.18em] text-phos">[ {title} ]</h2>
        {right ? <div className="flex min-w-0 items-center gap-2 text-2xs text-dim">{right}</div> : null}
      </header>
      <div className={`min-h-0 flex-1 ${bodyClassName}`}>{children}</div>
    </section>
  );
}

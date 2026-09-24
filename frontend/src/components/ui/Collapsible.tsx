import type { ReactNode } from "react";

/** Native disclosure: keyboard accessible and needs no client state. */
export function Collapsible({ summary, children, defaultOpen = false }: { summary: ReactNode; children: ReactNode; defaultOpen?: boolean }) {
  return (
    <details open={defaultOpen} className="group">
      <summary className="cursor-pointer select-none list-none text-sm text-cyan hover:underline [&::-webkit-details-marker]:hidden">
        <span className="inline-block w-4 text-dim group-open:hidden">+</span>
        <span className="hidden w-4 text-dim group-open:inline-block">-</span>
        {summary}
      </summary>
      <div className="mt-2">{children}</div>
    </details>
  );
}

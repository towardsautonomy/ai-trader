import Link from "next/link";
import type { ReactNode } from "react";

export function PageHeader({ title, right }: { title: string; right?: ReactNode }) {
  return (
    <div className="mb-2 flex items-center gap-4">
      <Link href="/" className="btn">
        &lt;- terminal
      </Link>
      <h1 className="text-sm font-bold uppercase tracking-[0.18em] text-phos">{title}</h1>
      {right ? <div className="ml-auto flex items-center gap-3">{right}</div> : null}
    </div>
  );
}

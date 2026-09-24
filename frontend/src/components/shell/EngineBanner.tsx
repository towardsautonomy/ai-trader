"use client";

import { useStatus } from "@/hooks/queries";
import { engineProblems } from "@/lib/engine";

/**
 * The API answering does not mean the engine is running. When a loop is
 * overdue, or the exit/watchdog loop is erroring, stops and the kill switch
 * may not be enforced: say so on every page until it clears.
 */
export function EngineBanner() {
  const status = useStatus();
  const problems = engineProblems(status.data);
  if (problems.length === 0) return null;
  return (
    <div role="alert" className="border-b-2 border-danger bg-danger/20 px-3 py-1.5 text-sm">
      <div>
        <span className="font-bold tracking-wider text-danger">
          <span className="animate-blink">!!</span> ENGINE MAY BE WEDGED
        </span>
        <span className="ml-3 text-fg">
          The API is answering but the trading engine is not keeping up. Stops, targets and the kill switch may not be enforced. Fallback from a shell in the backend
          directory:
        </span>
        <code className="ml-2 select-all whitespace-nowrap border border-line bg-panel px-1.5 text-amber">./ait kill --flatten</code>
      </div>
      <ul className="mt-0.5 text-xs text-fg">
        {problems.map((p, i) => (
          <li key={i} className="break-words">
            <span className="text-danger">- </span>
            {p.text}
          </li>
        ))}
      </ul>
    </div>
  );
}

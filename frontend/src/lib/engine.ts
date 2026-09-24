import type { LoopHeartbeat, Status } from "./types";

/** Loops whose failure means open positions are no longer protected. */
const CRITICAL_LOOPS = ["exit", "watchdog"];

export interface EngineProblem {
  loop: string;
  text: string;
}

export function engineLoops(status: Status | undefined): [string, LoopHeartbeat][] {
  return Object.entries(status?.engine ?? {});
}

/**
 * Reasons to believe the engine is wedged behind a healthy API: any loop
 * overdue, or the exit/watchdog loop reporting an error. Empty when the API
 * runs without an engine (engine is {}).
 */
export function engineProblems(status: Status | undefined): EngineProblem[] {
  const out: EngineProblem[] = [];
  for (const [loop, hb] of engineLoops(status)) {
    if (hb.overdue) out.push({ loop, text: `${loop} loop has not ticked for ${Math.round(hb.seconds_since_tick)}s (runs every ${hb.interval}s)` });
    if (hb.last_error && CRITICAL_LOOPS.includes(loop)) out.push({ loop, text: `${loop} loop error: ${hb.last_error}` });
  }
  return out;
}

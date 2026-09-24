import type { DecisionStatus, EventLevel, Instrument, Stance } from "./types";

export const DASH = "--";

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function num(v: number | null | undefined, digits = 2): string {
  if (!isNum(v)) return DASH;
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function usd(v: number | null | undefined, digits = 2): string {
  if (!isNum(v)) return DASH;
  return `$${num(v, digits)}`;
}

export function signedUsd(v: number | null | undefined, digits = 2): string {
  if (!isNum(v)) return DASH;
  return `${v < 0 ? "-" : "+"}$${num(Math.abs(v), digits)}`;
}

export function signedPct(v: number | null | undefined, digits = 2): string {
  if (!isNum(v)) return DASH;
  return `${v < 0 ? "-" : "+"}${num(Math.abs(v), digits)}%`;
}

export function signedR(v: number | null | undefined): string {
  if (!isNum(v)) return DASH;
  return `${v < 0 ? "-" : "+"}${num(Math.abs(v), 2)}R`;
}

export function pct(v: number | null | undefined, digits = 1): string {
  if (!isNum(v)) return DASH;
  return `${num(v, digits)}%`;
}

/** Tailwind text colour for a signed P&L-like value. */
export function pnlClass(v: number | null | undefined): string {
  if (!isNum(v) || v === 0) return "text-fg";
  return v > 0 ? "text-phos" : "text-danger";
}

export function clock(ts: string | null | undefined, withMs = false): string {
  if (!ts) return DASH;
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return DASH;
  const p = (n: number, w = 2) => String(n).padStart(w, "0");
  const base = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  return withMs ? `${base}.${p(d.getMilliseconds(), 3)}` : base;
}

export function dateTime(ts: string | null | undefined): string {
  if (!ts) return DASH;
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return DASH;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${clock(ts)}`;
}

/** 3725 -> "1h 02m 05s"; used for session countdowns. */
export function duration(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const p = (n: number) => String(n).padStart(2, "0");
  if (h > 0) return `${h}h ${p(m)}m ${p(sec)}s`;
  return `${p(m)}m ${p(sec)}s`;
}

export function minutesBetween(fromTs: string, toMs: number): number {
  return Math.max(0, Math.floor((toMs - new Date(fromTs).getTime()) / 60000));
}

export function instrumentLabel(inst: Instrument, fallback: string): string {
  if (!inst.right) return inst.symbol || fallback;
  const right = inst.right.toUpperCase().startsWith("C") ? "C" : "P";
  return `${inst.symbol} ${inst.expiry ?? ""} ${inst.strike ?? ""}${right}`.replace(/\s+/g, " ").trim();
}

/** `[###-------]` style meter. ratio is clamped to 0..1. */
export function meterBar(ratio: number, width = 10): string {
  const r = Number.isFinite(ratio) ? Math.min(1, Math.max(0, ratio)) : 0;
  const filled = Math.round(r * width);
  return `[${"#".repeat(filled)}${"-".repeat(width - filled)}]`;
}

export const AGENT_ABBR: Record<string, string> = {
  scout: "SCT",
  regime: "RGM",
  momentum: "MOM",
  mean_reversion: "MR",
  volatility: "VOL",
  skeptic: "SKP",
  portfolio_manager: "PM",
  position_manager: "POS",
  reviewer: "REV",
};

export function agentAbbr(agent: string): string {
  return AGENT_ABBR[agent] ?? agent.slice(0, 3).toUpperCase();
}

export const STANCE_GLYPH: Record<Stance, string> = { bullish: "▲", bearish: "▼", neutral: "▬" };

export function stanceClass(stance: string): string {
  if (stance === "bullish") return "text-phos";
  if (stance === "bearish") return "text-danger";
  return "text-dim";
}

export const LEVEL_CLASS: Record<EventLevel, string> = {
  info: "text-fg",
  warn: "text-amber",
  error: "text-danger",
  critical: "text-danger font-bold",
};

export const LEVEL_TAG: Record<EventLevel, string> = { info: "INFO", warn: "WARN", error: "ERR ", critical: "CRIT" };

export const DECISION_STATUS_CLASS: Record<DecisionStatus, string> = {
  deliberating: "text-cyan",
  executed: "text-phos",
  skipped_by_pm: "text-dim",
  rejected_by_risk: "text-danger",
  unbuildable: "text-amber",
  unfilled: "text-amber",
  failed: "text-danger",
};

export function upper(s: string | null | undefined): string {
  return (s ?? "").replace(/_/g, " ").toUpperCase();
}

export function truncate(s: string, n: number): string {
  return s.length <= n ? s : `${s.slice(0, n - 1)}…`;
}

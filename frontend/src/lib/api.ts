import type {
  AgentRow,
  CloseResponse,
  DecisionSummary,
  DecisionTrace,
  EquityPoint,
  EventLevel,
  History,
  KillLevel,
  KillResponse,
  Lesson,
  LLMCall,
  Mode,
  ModeResponse,
  Order,
  Position,
  PositionTrace,
  RearmResponse,
  Readiness,
  RobinhoodSnapshot,
  Stats,
  Status,
  TraderEvent,
} from "./types";

const BACKEND_PORT = process.env.NEXT_PUBLIC_API_PORT ?? "8400";

/** Backend base URL. With NEXT_PUBLIC_API_URL unset, the browser talks to the backend on the
 *  same host it loaded this page from, so one build works on localhost and from a remote machine. */
export function apiUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_URL;
  if (configured) return configured.replace(/\/+$/, "");
  if (typeof window === "undefined") return `http://127.0.0.1:${BACKEND_PORT}`;
  return `${window.location.protocol}//${window.location.hostname}:${BACKEND_PORT}`;
}

export function wsUrl(): string {
  return `${apiUrl().replace(/^http/, "ws")}/ws`;
}
const API_TOKEN = process.env.NEXT_PUBLIC_API_TOKEN ?? "";

const TIMEOUT_MS = 8000;

/** kind "network": the backend could not be reached at all. kind "http": it answered with an error. */
export class ApiError extends Error {
  constructor(
    readonly kind: "network" | "http",
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function isUnreachable(error: unknown): boolean {
  return error instanceof ApiError && error.kind === "network";
}

export function errorText(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}

type Params = Record<string, string | number | undefined | null>;

function buildUrl(path: string, params?: Params): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const q = qs.toString();
  return `${apiUrl()}${path}${q ? `?${q}` : ""}`;
}

async function request<T>(path: string, init?: { params?: Params; body?: unknown }): Promise<T> {
  const mutating = init?.body !== undefined;
  const headers: Record<string, string> = {};
  if (mutating) {
    headers["Content-Type"] = "application/json";
    if (API_TOKEN) headers["X-API-Token"] = API_TOKEN;
  }
  let res: Response;
  try {
    res = await fetch(buildUrl(path, init?.params), {
      method: mutating ? "POST" : "GET",
      headers,
      body: mutating ? JSON.stringify(init.body) : undefined,
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch (e) {
    const why = e instanceof DOMException && e.name === "TimeoutError" ? `timed out after ${TIMEOUT_MS / 1000}s` : errorText(e);
    throw new ApiError("network", `cannot reach ${apiUrl()} (${why})`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body: unknown = await res.json();
      if (body && typeof body === "object" && "detail" in body) {
        const d = (body as { detail: unknown }).detail;
        detail = typeof d === "string" ? d : JSON.stringify(d);
      }
    } catch {
      // non-JSON error body: keep the status text
    }
    throw new ApiError("http", `HTTP ${res.status}: ${detail}`, res.status);
  }
  return (await res.json()) as T;
}

export const api = {
  status: () => request<Status>("/api/status"),
  positions: (status: "open" | "closed" | "all", limit = 100) =>
    request<Position[]>("/api/positions", { params: { status, limit } }),
  position: (id: string) => request<PositionTrace>(`/api/positions/${encodeURIComponent(id)}`),
  closePosition: (id: string) => request<CloseResponse>(`/api/positions/${encodeURIComponent(id)}/close`, { body: {} }),
  decisions: (p: { limit?: number; status?: string; symbol?: string } = {}) =>
    request<DecisionSummary[]>("/api/decisions", { params: p }),
  decision: (id: string) => request<DecisionTrace>(`/api/decisions/${encodeURIComponent(id)}`),
  llmCall: (id: number) => request<LLMCall>(`/api/llm_calls/${id}`),
  events: (p: { limit?: number; after_id?: number; before_id?: number; level?: EventLevel; kind?: string; q?: string } = {}) =>
    request<TraderEvent[]>("/api/events", { params: p }),
  orders: (limit = 100) => request<Order[]>("/api/orders", { params: { limit } }),
  equity: (hours = 24) => request<EquityPoint[]>("/api/equity", { params: { hours } }),
  agents: () => request<AgentRow[]>("/api/agents"),
  lessons: (limit = 50) => request<Lesson[]>("/api/lessons", { params: { limit } }),
  stats: () => request<Stats>("/api/stats"),
  kill: (level: KillLevel, reason: string) => request<KillResponse>("/api/kill", { body: { level, reason } }),
  rearm: (confirm: string) => request<RearmResponse>("/api/kill/rearm", { body: { confirm } }),
  robinhood: (fresh = false) => request<RobinhoodSnapshot>("/api/robinhood", { params: { fresh: fresh ? "true" : undefined } }),
  readiness: () => request<Readiness>("/api/live/readiness"),
  history: () => request<History>("/api/history"),
  setMode: (mode: Mode, confirm = "", code = "") => request<ModeResponse>("/api/mode", { body: { mode, confirm, code } }),
};

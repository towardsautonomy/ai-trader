"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

const FAST = 2000;
const MEDIUM = 5000;
const SLOW = 15000;
const SLOWER = 30000;

export const keys = {
  status: ["status"] as const,
  positions: (status: string) => ["positions", status] as const,
  position: (id: string) => ["position", id] as const,
  decisions: (limit: number) => ["decisions", limit] as const,
  decision: (id: string) => ["decision", id] as const,
  llmCall: (id: number) => ["llm_call", id] as const,
  orders: ["orders"] as const,
  equity: (hours: number) => ["equity", hours] as const,
  agents: ["agents"] as const,
  lessons: ["lessons"] as const,
  stats: ["stats"] as const,
  robinhood: ["robinhood"] as const,
  readiness: ["readiness"] as const,
  history: ["history"] as const,
};

export const useStatus = () => useQuery({ queryKey: keys.status, queryFn: api.status, refetchInterval: FAST });

export const useOpenPositions = () =>
  useQuery({ queryKey: keys.positions("open"), queryFn: () => api.positions("open"), refetchInterval: FAST });

export const useDecisions = (limit = 40) =>
  useQuery({ queryKey: keys.decisions(limit), queryFn: () => api.decisions({ limit }), refetchInterval: MEDIUM });

/** A trace keeps changing while the decision deliberates or its position is open, so it polls. */
export const useDecisionTrace = (id: string) =>
  useQuery({ queryKey: keys.decision(id), queryFn: () => api.decision(id), refetchInterval: MEDIUM });

export const usePositionTrace = (id: string) =>
  useQuery({ queryKey: keys.position(id), queryFn: () => api.position(id), refetchInterval: MEDIUM });

export const useLLMCall = (id: number, enabled: boolean) =>
  useQuery({ queryKey: keys.llmCall(id), queryFn: () => api.llmCall(id), enabled, staleTime: Infinity });

export const useOrders = () => useQuery({ queryKey: keys.orders, queryFn: () => api.orders(200), refetchInterval: SLOW });

export const useEquity = (hours: number) =>
  useQuery({ queryKey: keys.equity(hours), queryFn: () => api.equity(hours), refetchInterval: SLOW });

export const useStats = () => useQuery({ queryKey: keys.stats, queryFn: api.stats, refetchInterval: SLOW });

export const useAgents = () => useQuery({ queryKey: keys.agents, queryFn: api.agents, refetchInterval: SLOW });

export const useLessons = () => useQuery({ queryKey: keys.lessons, queryFn: () => api.lessons(100), refetchInterval: SLOWER });

export const useRobinhood = () => useQuery({ queryKey: keys.robinhood, queryFn: () => api.robinhood(), refetchInterval: SLOWER });

/** Every readiness check talks to Robinhood, so it polls slowly and only where it is shown. */
export const useReadiness = () => useQuery({ queryKey: keys.readiness, queryFn: api.readiness, refetchInterval: 60000 });

export const useHistory = () => useQuery({ queryKey: keys.history, queryFn: api.history, refetchInterval: SLOW });

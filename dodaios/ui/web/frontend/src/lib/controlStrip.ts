/**
 * The main-screen control strip (ADR-0058): the contract of `/api/control-strip`, `/api/stop`
 * and `/api/stop/resume`, and the hooks over them. Every state is a fixed code; the screen renders
 * it in the owner's language. Nothing here reads a prompt, a key or a memory.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export type StripSummary =
  | "error"
  | "stopping"
  | "partly_stopped"
  | "waiting"
  | "working"
  | "speaking"
  | "thinking"
  | "listening"
  | "stopped"
  | "idle"
  | "unknown";
export type Level = "active" | "idle" | "unknown";
export type Outcome = "requested" | "confirmed" | "still_active" | "unknown";

export interface EngineUse {
  provider: string;
  role: "main" | "reviewer" | "synthesizer";
  locality: "local" | "remote" | "unknown";
}
export interface Flow {
  category: string;
  destination: string;
}
export interface PendingDecision {
  trace_id: string;
  mission_id: string;
  tool_name: string;
  risk_tier: string;
  reason: string;
  args_preview: string;
}
export interface StopReport {
  generation: number;
  state: "stopped" | "partly_stopped";
  repeated: boolean;
  results: { name: string; outcome: Outcome; detail: string }[];
}
export interface ControlStrip {
  state: StripSummary;
  not_confirmed: string[];
  door_open: boolean;
  generation: number;
  indicators: { audio: Level; jobs: number | null; approvals: number | null };
  engine: {
    turn_id: string | null;
    inference: "local" | "remote" | "mixed" | "unknown" | "none";
    engines: EngineUse[];
    network_tools: "not_observed" | "observed";
    planned: Flow[];
    attempted: Flow[];
    observability: "declared" | "none";
    refused: string[];
    /** `running`: the engine listed is the one chosen at the start, not yet confirmed to answer. */
    state: "running" | "answered" | "failed";
    requested: string | null;
    fell_back: boolean;
  };
  jobs: {
    missions: { id: string; state: string }[];
    computer_use_runs: number;
    background_agents: number;
  };
  approvals: PendingDecision[];
  stop: StopReport | null;
}

export const CONTROL_STRIP_QUERY_KEY = ["control-strip"] as const;
/** A reading older than this is not trusted: two missed polls and a margin. */
export const STALE_AFTER_MS = 9000;

/**
 * Whether what is on screen may still be called current. A failed read, no reading at all, or one
 * older than {@link STALE_AFTER_MS} (a lost connection, a hidden window whose polling paused) is
 * NOT current: the screen then shows "unknown", never the last value as if it were true.
 */
export function isStale(input: { hasData: boolean; failed: boolean; updatedAt: number; now: number }): boolean {
  if (!input.hasData || input.failed) return true;
  return input.now - input.updatedAt > STALE_AFTER_MS;
}
/** Slow enough to be cheap, fast enough that "Stopped" never lingers after a change. */
const POLL_MS = 3000;

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (body as { detail?: unknown }).detail;
    throw new Error(typeof detail === "string" ? detail : `HTTP ${res.status}`);
  }
  return body as T;
}

export function useControlStrip() {
  return useQuery<ControlStrip, Error>({
    queryKey: CONTROL_STRIP_QUERY_KEY,
    queryFn: () => request<ControlStrip>("/api/control-strip"),
    refetchInterval: POLL_MS,
    retry: false,
  });
}

export function useStop() {
  const client = useQueryClient();
  return useMutation<ControlStrip, Error, void>({
    mutationFn: () => request<ControlStrip>("/api/stop", { method: "POST" }),
    onSuccess: (state) => client.setQueryData(CONTROL_STRIP_QUERY_KEY, state),
  });
}

export function useResume() {
  const client = useQueryClient();
  return useMutation<ControlStrip, Error, void>({
    mutationFn: () => request<ControlStrip>("/api/stop/resume", { method: "POST" }),
    onSuccess: (state) => client.setQueryData(CONTROL_STRIP_QUERY_KEY, state),
  });
}

/** Approve or deny one paused mission tool call, through the existing mission routes. */
export function useDecide() {
  const client = useQueryClient();
  return useMutation<unknown, Error, { decision: "approve" | "deny"; d: PendingDecision }>({
    mutationFn: ({ decision, d }) =>
      request(`/api/missions/${encodeURIComponent(d.mission_id)}/tool-approvals/${encodeURIComponent(d.trace_id)}/${decision}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: decision === "deny" ? JSON.stringify({ reason: "user_denied" }) : undefined,
      }),
    onSettled: () => client.invalidateQueries({ queryKey: CONTROL_STRIP_QUERY_KEY }),
  });
}

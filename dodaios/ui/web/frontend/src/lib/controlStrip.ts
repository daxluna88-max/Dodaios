/**
 * The main-screen control strip (ADR-0058): the contract of `/api/control-strip`, `/api/stop`
 * and `/api/stop/resume`, and the hooks over them. Every state is a fixed code; the screen renders
 * it in the owner's language. Nothing here reads a prompt, a key or a memory.
 */
import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";

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
  /** The model resolved at dispatch; null = not known. */
  model?: string | null;
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
  /** Digest of the exact call asked about; the decision echoes it (the server refuses a mismatch). */
  call_fingerprint?: string;
  requested_at_ns?: number;
  expires_at_ns?: number;
}
export interface MissionJob {
  id: string;
  state: string;
  /** First characters of the mission's prompt; null = not known. */
  title?: string | null;
  started_ms?: number | null;
  /** The tool the mission waits on, only while a decision for it is pending. */
  tool?: string | null;
  /** Never recorded in the mission header: always null today (shown as "not observed"). */
  engine?: string | null;
}
export interface MemoryInfo {
  /** null = the config could not be read. */
  learning_enabled: boolean | null;
  /** null = the vault could not be read (unknown, not zero). */
  count: number | null;
  archive_is_deletion: false;
  per_note_approval: false;
  backups_and_indexes_covered: false;
}
export interface Limits {
  network_tools: "not_observed" | "observed";
  /** null = the guard status could not be read. */
  python_guard_enforced: boolean | null;
  os_enforcement_verified: boolean | null;
  voice_stop: Record<string, Outcome | undefined>;
  turn_phase: string | null;
  tools_used_observed: boolean;
}
export interface StopReport {
  generation: number;
  state: "stopped" | "partly_stopped";
  repeated: boolean;
  results: { name: string; outcome: Outcome; detail: string }[];
}
export interface ControlStrip {
  /** Ordering fields (block 10); absent on an older server, which then gets no ordering guard. */
  boot_id?: string;
  seq?: number;
  etag?: string;
  /** Epoch ms on the SERVER clock: for display ("as of"), never compared with the browser's. */
  server_time?: number;
  client_seq?: number | null;
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
    state: "running" | "answered" | "failed" | "superseded";
    requested: string | null;
    fell_back: boolean;
    /** planned -> attempted -> completed | failed | superseded (STOP overtook the turn). */
    phase?: "planned" | "attempted" | "completed" | "failed" | "superseded";
    /** `planned` is a declared list, never a full inventory (history, memory, files, tool output). */
    planned_partial?: boolean;
    generation?: number | null;
    run_id?: string | null;
    earlier_attempts?: string[];
  };
  jobs: {
    missions: MissionJob[];
    computer_use_runs: number;
    background_agents: number;
  };
  approvals: PendingDecision[];
  memory?: MemoryInfo;
  limits?: Limits;
  stop: StopReport | null;
}

export const CONTROL_STRIP_QUERY_KEY = ["control-strip"] as const;
/** A reading older than this is not trusted: two missed polls and a margin. */
export const STALE_AFTER_MS = 9000;

/**
 * Whether what is on screen may still be called current. A failed read, no reading at all, one
 * older than {@link STALE_AFTER_MS} (a lost connection, a hidden window whose polling paused) or
 * an offline browser is NOT current: the screen then shows "unknown", never the last value as if
 * it were true. `updatedAt` must be the time of the last SUCCESSFULLY APPLIED read.
 */
export function isStale(input: {
  hasData: boolean;
  failed: boolean;
  updatedAt: number;
  now: number;
  offline?: boolean;
}): boolean {
  if (!input.hasData || input.failed || input.offline) return true;
  return input.now - input.updatedAt > STALE_AFTER_MS;
}
/** Slow enough to be cheap, fast enough that "Stopped" never lingers after a change. */
const POLL_MS = 3000;

/**
 * Orders the replies of the control strip. A reply is applied only if it is newer than the last
 * one applied: same server process, `seq` higher, STOP `generation` not lower. A reply of an
 * earlier process (restart) is dropped; the first of a new process starts a new epoch. A
 * reply whose request was sent before a STOP / resume began (below the barrier) is dropped, so a
 * GET that was in flight cannot repaint the state that STOP just replaced.
 */
export class StripOrderer {
  private boots: string[] = [];
  private lastSeq = -1;
  private lastGeneration = -1;
  private nextClientSeq = 1;
  private barrier = 0;
  current: ControlStrip | undefined;
  /** Browser time of the last reply that was APPLIED (0 = none yet). */
  lastOkAt = 0;

  /** The sequence number and known STOP generation to send with the next poll. */
  nextRequest(): { clientSeq: number; knownGeneration: number } {
    return { clientSeq: this.nextClientSeq++, knownGeneration: Math.max(this.lastGeneration, 0) };
  }

  /** Called when STOP / resume starts: every poll sent until now may no longer be applied. */
  raiseBarrier(): void {
    this.barrier = this.nextClientSeq;
  }

  /** Whether the request numbered `clientSeq` was sent after the last barrier. */
  isAfterBarrier(clientSeq: number): boolean {
    return clientSeq >= this.barrier;
  }

  /** Apply `next` if it is newer than what is applied; returns whether it was. */
  accept(next: ControlStrip, now: number = Date.now(), clientSeq?: number): boolean {
    if (clientSeq !== undefined && !this.isAfterBarrier(clientSeq)) return false;
    const boot = next.boot_id;
    const seq = next.seq;
    if (boot !== undefined && seq !== undefined) {
      const known = this.boots.indexOf(boot);
      if (known === -1) {
        this.boots.push(boot);
        this.lastSeq = -1;
        this.lastGeneration = -1;
      } else if (known !== this.boots.length - 1) {
        return false; // a late reply of an earlier server process
      }
      if (seq <= this.lastSeq) return false;
      if (next.generation < this.lastGeneration) return false;
      this.lastSeq = seq;
    } else if (next.generation < this.lastGeneration) {
      return false; // an older server still cannot go back across a STOP generation
    }
    this.lastGeneration = Math.max(this.lastGeneration, next.generation);
    this.current = next;
    this.lastOkAt = now;
    return true;
  }
}

const orderers = new WeakMap<QueryClient, StripOrderer>();
/** One orderer per query client: the polls, STOP and resume of one screen share it. */
export function stripOrderer(client: QueryClient): StripOrderer {
  let o = orderers.get(client);
  if (!o) {
    o = new StripOrderer();
    orderers.set(client, o);
  }
  return o;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (body as { detail?: unknown }).detail;
    const code = detail && typeof detail === "object" ? (detail as { code?: unknown }).code : detail;
    throw new Error(typeof code === "string" ? code : `HTTP ${res.status}`);
  }
  return body as T;
}

/** What the screen may show: the current reading, or only a historical one (never both). */
export interface StripView {
  /** Set only while the last read succeeded, is recent and the browser is online. */
  current: ControlStrip | undefined;
  /** The last applied reading when it is NOT current: show it as history, with no controls. */
  historical: ControlStrip | undefined;
  /** Browser time of the last successfully applied read; 0 = none. */
  lastOkAt: number;
  failed: boolean;
}

/** Decide, from the query state, orderer and clock, what is current and what is only history. */
export function viewOf(input: {
  orderer: StripOrderer;
  isError: boolean;
  now: number;
  offline: boolean;
}): StripView {
  const { orderer, isError, now, offline } = input;
  const last = orderer.current;
  const stale = isStale({
    hasData: !!last, failed: isError, updatedAt: orderer.lastOkAt, now, offline,
  });
  return {
    current: stale ? undefined : last,
    historical: stale ? last : undefined,
    lastOkAt: orderer.lastOkAt,
    failed: isError,
  };
}

export function useControlStrip(): StripView & { isFetching: boolean } {
  const client = useQueryClient();
  const orderer = stripOrderer(client);
  const query = useQuery<ControlStrip, Error>({
    queryKey: CONTROL_STRIP_QUERY_KEY,
    queryFn: async ({ signal }) => {
      const { clientSeq, knownGeneration } = orderer.nextRequest();
      const url = `/api/control-strip?client_seq=${clientSeq}&known_generation=${knownGeneration}`;
      const reply = await request<ControlStrip>(url, { signal });
      // An out-of-order reply is dropped; the applied one stays. `lastOkAt` is not touched, so
      // a reply that was dropped never makes an old reading look fresh.
      if (!orderer.accept(reply, Date.now(), clientSeq) && orderer.current) return orderer.current;
      return reply;
    },
    refetchInterval: POLL_MS,
    retry: false,
  });
  // `isError` is read BEFORE any data: React Query keeps the last success in `data` after a
  // failed refetch, and that must never be shown as current.
  const offline = typeof navigator !== "undefined" && navigator.onLine === false;
  const view = viewOf({ orderer, isError: query.isError, now: Date.now(), offline });
  return { ...view, isFetching: query.isFetching };
}

/** Before STOP / resume is sent: abort polls in flight and refuse their replies. */
function beginWrite(client: QueryClient): void {
  stripOrderer(client).raiseBarrier();
  void client.cancelQueries({ queryKey: CONTROL_STRIP_QUERY_KEY });
}

/** Apply the reply of a write only if it is newer than what is on screen. */
function applyWrite(client: QueryClient, state: ControlStrip): void {
  if (stripOrderer(client).accept(state)) client.setQueryData(CONTROL_STRIP_QUERY_KEY, state);
}

export function useStop() {
  const client = useQueryClient();
  return useMutation<ControlStrip, Error, void>({
    mutationFn: () => {
      beginWrite(client);
      return request<ControlStrip>("/api/stop", { method: "POST" });
    },
    onSuccess: (state) => applyWrite(client, state),
  });
}

export function useResume() {
  const client = useQueryClient();
  return useMutation<ControlStrip, Error, void>({
    mutationFn: () => {
      beginWrite(client);
      return request<ControlStrip>("/api/stop/resume", { method: "POST" });
    },
    onSuccess: (state) => applyWrite(client, state),
  });
}

/**
 * Approve or deny ONE paused mission tool call, through the existing mission routes. The request
 * carries the identity the server gave (trace id in the path, `call_fingerprint` of the call the
 * owner looked at, and the STOP generation the screen knew); the server refuses a mismatch. The
 * one-use `approval_id` stays on the server and is never sent by the browser. There is no bulk
 * and no "always allow": one call per click.
 */
export function useDecide() {
  const client = useQueryClient();
  return useMutation<unknown, Error, { decision: "approve" | "deny"; d: PendingDecision; generation: number }>({
    mutationFn: ({ decision, d, generation }) =>
      request(`/api/missions/${encodeURIComponent(d.mission_id)}/tool-approvals/${encodeURIComponent(d.trace_id)}/${decision}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...(decision === "deny" ? { reason: "user_denied" } : {}),
          call_fingerprint: d.call_fingerprint ?? null,
          generation,
        }),
      }),
    onSettled: () => client.invalidateQueries({ queryKey: CONTROL_STRIP_QUERY_KEY }),
  });
}

/** Cancel one running mission (the existing cancel route). */
export function useCancelJob() {
  const client = useQueryClient();
  return useMutation<unknown, Error, { missionId: string }>({
    mutationFn: ({ missionId }) =>
      request(`/api/missions/${encodeURIComponent(missionId)}/cancel`, { method: "POST" }),
    onSettled: () => client.invalidateQueries({ queryKey: CONTROL_STRIP_QUERY_KEY }),
  });
}

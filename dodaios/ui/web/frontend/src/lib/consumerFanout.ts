/**
 * Consumer fan-out (ADR-0050): the contract of `dodaios/ui/web/consumer_fanout_routes.py`.
 * One question, several chosen engines, one ceiling for the whole run.
 */

export type EngineKind = "local" | "subscription" | "api";

export interface FanoutEngine {
  id: string;
  kind: EngineKind;
  local: boolean;
  /** Read from stored credentials / plan login without contacting the provider. */
  authenticated: boolean;
}

export interface FanoutStatus {
  available: boolean;
  reason: string;
  providers: FanoutEngine[];
}

export interface FanoutBranchRequest {
  provider: string;
  /** Null = the owner gave no estimate; the backend refuses it unless unknown prices are allowed. */
  est_cost: number | null;
}

export interface FanoutBranchResult {
  branch: string;
  provider: string;
  status: "ok" | "refused" | "failed" | "timeout" | "cancelled" | "stale";
  text: string;
  reason: string;
}

export interface FanoutReportBranch {
  branch: string;
  provider: string;
  status: string;
  cost_basis: string | null;
  first_token_s: number | null;
  total_s: number | null;
}

export interface FanoutFinal {
  status: "ok" | "no_input" | "refused" | "failed" | "timeout" | "stale" | "cancelled";
  text: string;
  reason: string;
  /** Branch ids whose answers were offered to the synthesizer. */
  sources: string[];
  /** Answers kept back because the synthesizer is not on this computer. */
  withheld: number;
}

export interface FanoutResult {
  /** Null when no final answer was asked for. */
  final?: FanoutFinal | null;
  branches: FanoutBranchResult[];
  report: { budget_reserved: number; budget_limit: number; branches: FanoutReportBranch[] };
  egress: Array<{ branch: string; destination: string; chars: number; items: number }>;
}

async function readJson<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (body as { detail?: unknown }).detail;
    throw new Error(typeof detail === "string" ? detail : `HTTP ${res.status}`);
  }
  return body as T;
}

export async function fetchFanoutStatus(): Promise<FanoutStatus> {
  return readJson<FanoutStatus>(await fetch("/api/consumer/status"));
}

export async function runFanout(request: {
  task: string;
  branches: FanoutBranchRequest[];
  budget: number;
  allow_unknown_cost: boolean;
  /** Omitted: compare the answers only. */
  synthesizer?: FanoutBranchRequest;
}): Promise<FanoutResult> {
  return readJson<FanoutResult>(
    await fetch("/api/consumer/fanout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    }),
  );
}

/** "" and anything that is not a finite number >= 0 mean "no estimate". */
export function parseCost(raw: string): number | null {
  const text = raw.trim().replace(",", ".");
  if (text === "") return null;
  const value = Number(text);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

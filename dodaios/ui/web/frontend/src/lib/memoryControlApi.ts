/**
 * Client for the controllable-memory API (`dodaios/ui/web/memory_control_routes.py`, ADR-0061).
 * Written, not type-checked and not run.
 */
export interface MemoryProposal {
  id: string;
  product: string;
  state: "proposed" | "approved" | "rejected";
  target: string;
  operation: "add" | "replace" | "remove";
  text: string;
  before: string;
  evidence: string;
  source: string;
  local_only: boolean;
  provenance: "restricted" | "unlabelled";
  created: number;
}

export interface StoreResult {
  store: string;
  done: boolean;
  detail: string;
}

export interface DeletionReport {
  ok: true;
  mode: "logical" | "permanent";
  path: string;
  trash_id: string;
  restore_until: number;
  stores: StoreResult[];
  derived: { path: string; why: string }[];
  residual: string[];
}

export interface TrashItem {
  id: string;
  path: string;
  deleted_at: number;
  restore_until: number;
}

export interface ControlStatus {
  product: string;
  learning_enabled: boolean | null;
  require_approval: boolean;
  pending: number;
  trash: number;
  retention_days: number;
  backups: { keeps_newest: number; rotation: string };
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const detail = (payload as { detail?: unknown } | null)?.detail;
    throw new Error(typeof detail === "string" ? detail : `HTTP ${response.status}`);
  }
  return payload as T;
}

const post = <T>(url: string, body: unknown = {}) =>
  call<T>(url, { method: "POST", body: JSON.stringify(body) });

export const fetchControlStatus = () => call<ControlStatus>("/api/memory-control/status");
export const fetchProposals = () =>
  call<{ proposals: MemoryProposal[] }>("/api/memory-control/proposals");
/** One note. `text` is the owner's edit; there is no bulk call by design. */
export const approveProposal = (id: string, text?: string) =>
  post(`/api/memory-control/proposals/${encodeURIComponent(id)}/approve`, { text: text ?? null });
export const rejectProposal = (id: string) =>
  post(`/api/memory-control/proposals/${encodeURIComponent(id)}/reject`);
export const deleteMemoryPage = (path: string, mode: "logical" | "permanent") =>
  post<DeletionReport>("/api/memory-control/pages/delete", {
    path,
    mode,
    confirm_permanent: mode === "permanent",
  });
export const fetchTrash = () => call<{ items: TrashItem[] }>("/api/memory-control/trash");
export const restoreTrash = (id: string) => post("/api/memory-control/trash/restore", { id });
export const deleteTrashForever = (id: string) =>
  post<DeletionReport>("/api/memory-control/trash/delete", { id });
export const EXPORT_URL = "/api/memory-control/export";

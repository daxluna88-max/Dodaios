import { useCallback, useEffect, useState } from "react";
import type { DashboardWidget } from "./NeuralApp";

const PREFIX = "jarvis.neural.dashboard.v1";
type Snapshot = { version: 1; widgets: DashboardWidget[]; hidden: string[] };
const defaults: DashboardWidget[] = [
  { id: "projects", title: "Progetti" }, { id: "calendar", title: "Calendario" },
  { id: "documents", title: "Documenti" }, { id: "tasks", title: "Attività" }
];
function key(product: string, account: string) {
  if (!/^[\w-]{1,80}$/.test(product) || !/^[\w-]{1,120}$/.test(account))
    throw new Error("Identità dashboard non valida");
  return PREFIX + "." + product + "." + account;
}
function read(storageKey: string): Snapshot {
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return { version: 1, widgets: defaults, hidden: [] };
    const p: unknown = JSON.parse(raw);
    if (!p || typeof p !== "object" || !("version" in p) || p.version !== 1 ||
        !("widgets" in p) || !Array.isArray(p.widgets) ||
        !("hidden" in p) || !Array.isArray(p.hidden)) throw Error("invalid");
    const widgets = p.widgets.filter((x: unknown): x is DashboardWidget =>
      !!x && typeof x === "object" && "id" in x && typeof x.id === "string" &&
      "title" in x && typeof x.title === "string").slice(0, 60);
    const hidden = p.hidden.filter((x: unknown): x is string => typeof x === "string").slice(0, 60);
    return { version: 1, widgets, hidden };
  } catch {
    return { version: 1, widgets: defaults, hidden: [] };
  }
}
export function useNeuralDashboard(product: "consumer" | "work", accountId: string) {
  const storageKey = key(product, accountId);
  const [snapshot, setSnapshot] = useState<Snapshot>(() => read(storageKey));
  useEffect(() => { setSnapshot(read(storageKey)); }, [storageKey]);
  const persist = useCallback((fn: (s: Snapshot) => Snapshot) => {
    setSnapshot(old => {
      const next = fn(old);
      try { localStorage.setItem(storageKey, JSON.stringify(next)); } catch { /* private mode or full storage */ }
      return next;
    });
  }, [storageKey]);
  const add = useCallback((title: string) => {
    const trimmed = title.trim().slice(0, 48);
    if (!trimmed) return;
    const id = "user-" + (globalThis.crypto?.randomUUID?.() ?? Date.now().toString(36));
    persist(s => s.widgets.length >= 60 ? s : { ...s, widgets: [...s.widgets, { id, title: trimmed }] });
  }, [persist]);
  const remove = useCallback((id: string) => {
    persist(s => ({ ...s, hidden: [...new Set([...s.hidden, id])] }));
  }, [persist]);
  const restore = useCallback((id: string) => {
    persist(s => ({ ...s, hidden: s.hidden.filter(x => x !== id) }));
  }, [persist]);
  const reset = useCallback(() => persist(() => ({ version: 1, widgets: defaults, hidden: [] })), [persist]);
  return { widgets: snapshot.widgets.filter(x => !snapshot.hidden.includes(x.id)),
    add, remove, restore, reset };
}

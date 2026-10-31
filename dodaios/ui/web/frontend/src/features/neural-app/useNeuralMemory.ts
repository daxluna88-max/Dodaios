import { useEffect, useReducer } from "react";
import type { MemoryNode } from "./NeuralApp";

/** Display-only events supplied by an authenticated host memory adapter. */
export type NeuralMemoryEvent =
  | { type: "snapshot"; product: "consumer" | "work"; accountId: string; nodes: MemoryNode[] }
  | { type: "activated"; product: "consumer" | "work"; accountId: string; nodeId: string }
  | { type: "forgotten"; product: "consumer" | "work"; accountId: string; nodeId: string }
  | { type: "clear"; product: "consumer" | "work"; accountId: string };
type State = { nodes: MemoryNode[]; active: string | null };
const empty: State = { nodes: [], active: null };
function scrub(raw: MemoryNode[]): MemoryNode[] {
  const ids = new Set<string>();
  return raw.filter(n => {
    if (!n || typeof n.id !== "string" || typeof n.label !== "string" ||
        typeof n.category !== "string" || n.id.length > 120 || ids.has(n.id)) return false;
    ids.add(n.id); return true;
  }).slice(0, 50).map(n => ({
    id: n.id, label: n.label.slice(0, 100), category: n.category.slice(0, 40),
    activity: Number.isFinite(n.activity) ? Math.max(0, Math.min(1, n.activity!)) : 0
  }));
}
function reducer(state: State, event: NeuralMemoryEvent): State {
  switch (event.type) {
    case "snapshot": return { nodes: scrub(event.nodes), active: null };
    case "activated": return state.nodes.some(n=>n.id===event.nodeId)
      ? { nodes: state.nodes.map(n=>({ ...n, activity: n.id===event.nodeId?1:0 })), active: event.nodeId }
      : state;
    case "forgotten": return { nodes: state.nodes.filter(n=>n.id!==event.nodeId), active: state.active===event.nodeId?null:state.active };
    case "clear": return empty;
  }
}
/**
 * No network, no inferred memories and no subscription to raw database content.
 * Host must enforce authorization and provide sanitized events of the exact user/product.
 */
export function useNeuralMemory(product: "consumer" | "work", accountId: string,
  subscribe?: (listener: (event: NeuralMemoryEvent) => void) => () => void) {
  const [state, dispatch] = useReducer(reducer, empty);
  useEffect(() => {
    dispatch({ type: "clear", product, accountId });
    if (!subscribe) return;
    let live = true;
    const unsubscribe = subscribe(event => {
      if (live && event.product === product && event.accountId === accountId) dispatch(event);
    });
    return () => { live = false; unsubscribe(); };
  }, [product, accountId, subscribe]);
  return state;
}

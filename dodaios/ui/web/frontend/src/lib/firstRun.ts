/**
 * The guided first run (issue 18): the contract of `/api/onboarding/first-run` and a hook over it.
 *
 * Choices are saved as the owner goes (so Back and a closed window lose nothing) but are only written to the
 * configuration by `apply`, one explicit step. In a replay from Settings (`preview`) nothing is sent to the server:
 * an installed app's approval mode and memory switch are never changed by walking the guide again.
 */
import { useCallback, useEffect, useRef, useState } from "react";

export type Interaction = "text" | "voice" | "both";
export type ToneChoice = "keep" | "adapt";
export type MemoryChoice = "off" | "on";

export interface FirstRunChoices {
  owner_name: string | null;
  space_name: string | null;
  interaction: Interaction;
  personality_skip: boolean;
  personality_tone: ToneChoice;
  personality_preset: string | null;
  memory: MemoryChoice;
}

export interface FirstRunEngine {
  provider: string;
  kind: "local" | "subscription" | "api" | null;
  local: boolean | null;
  installed: boolean;
  allowed: boolean;
  authenticated: boolean;
  /** A conversation can be attempted. Never "it was seen to answer": `verified` is always false here. */
  ready: boolean;
  verified: boolean;
  reason: string;
}

export interface FirstRunSummary {
  product: "consumer" | "work";
  interaction: Interaction;
  personality: { preset: string; mode: string };
  engine: FirstRunEngine;
  memory: { learning_enabled: boolean };
  actions: { approval_mode: string };
  start: "start" | "explore";
}

export interface FirstRunState {
  product: "consumer" | "work";
  choices: FirstRunChoices;
  applied: boolean;
  presets: string[];
  default_preset: string;
  summary: FirstRunSummary | null;
}

const URL_BASE = "/api/onboarding/first-run";

/** The server's answer must look like a state, or it is not trusted (an old or odd server never crashes the guide). */
function isState(value: unknown): value is FirstRunState {
  const v = value as Partial<FirstRunState> | null;
  return Boolean(v && typeof v === "object" && v.choices && typeof v.choices === "object" && Array.isArray(v.presets));
}

async function callState(url: string, init?: RequestInit): Promise<FirstRunState> {
  const body = await call<unknown>(url, init);
  if (!isState(body)) throw new Error("unexpected answer");
  return body;
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (body as { detail?: unknown }).detail;
    const text =
      typeof detail === "string"
        ? detail
        : detail && typeof detail === "object" && "step" in detail
          ? `step ${String((detail as { step: unknown }).step)}`
          : `HTTP ${res.status}`;
    throw new Error(text);
  }
  return body as T;
}

export function useFirstRun(preview: boolean) {
  const [state, setState] = useState<FirstRunState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const latest = useRef<FirstRunState | null>(null);
  latest.current = state;

  const refresh = useCallback(async () => {
    try {
      setState(await callState(URL_BASE));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  /** Change some choices. In a replay they stay in this window only. */
  const update = useCallback(
    async (patch: Partial<FirstRunChoices>) => {
      const current = latest.current;
      if (!current) return;
      const choices = { ...current.choices, ...patch };
      const optimistic = { ...current, choices, applied: false };
      latest.current = optimistic;
      setState(optimistic);
      if (preview) return;
      try {
        const saved = await callState(URL_BASE, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(choices),
        });
        setState(saved);
        setError(null);
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [preview],
  );

  /** Write the choices to the configuration. Throws when a write failed: the run is then NOT applied. */
  const apply = useCallback(async () => {
    if (preview) return;
    setBusy(true);
    try {
      setState(await callState(`${URL_BASE}/apply`, { method: "POST" }));
      setError(null);
    } finally {
      setBusy(false);
    }
  }, [preview]);

  return { state, error, busy, update, apply, refresh };
}

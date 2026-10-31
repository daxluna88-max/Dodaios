/**
 * Personality data layer (issue 17): the shapes `personality_routes.py` returns and its writes.
 * Every write answers with the whole state, so the page swaps it in place.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export type PersonalityMode = "manual" | "adaptive" | "mixed";
export type Dimension = "formality" | "warmth" | "humor" | "concision";

export interface PersonalityProfile {
  schema_version: number;
  mode: PersonalityMode;
  preset: string | null;
  tone: string | null;
  formality: number | null;
  warmth: number | null;
  humor: number | null;
  concision: number | null;
  expressive_range: number;
  moods_allowed: string[];
  locked_mood: string | null;
}

export interface PersonalityState {
  product: string | null;
  status: "ok" | "migrated" | "defaults" | "newer";
  reason: string;
  profile: PersonalityProfile;
  effective: { mode: string; preset: string; tone: string } & Record<Dimension, number>;
  presets: Record<string, { tone: string } & Record<Dimension, number>>;
  default_preset: string;
  mood: {
    mood: string;
    intensity: number;
    origin: string;
    reason: string;
    expires_at: number | null;
    adaptation_on: boolean;
  };
  mood_available: boolean;
  simulated: true;
  tones: string[];
  moods: string[];
}

export type MoodAction =
  | { action: "set"; mood: string; intensity?: number }
  | { action: "hold" }
  | { action: "neutral" }
  | { action: "adaptation"; enabled: boolean };

export const PERSONALITY_QUERY_KEY = ["personality"] as const;

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (body as { detail?: unknown }).detail;
    throw new Error(typeof detail === "string" ? detail : `HTTP ${res.status}`);
  }
  return body as T;
}

const JSON_HEADERS = { "Content-Type": "application/json" };

export function usePersonality() {
  return useQuery<PersonalityState, Error>({
    queryKey: PERSONALITY_QUERY_KEY,
    queryFn: () => request<PersonalityState>("/api/personality"),
    retry: false,
  });
}

export function useSavePersonality() {
  const client = useQueryClient();
  return useMutation<PersonalityState, Error, Partial<PersonalityProfile>>({
    mutationFn: (profile) =>
      request<PersonalityState>("/api/personality", {
        method: "PUT",
        headers: JSON_HEADERS,
        body: JSON.stringify(profile),
      }),
    onSuccess: (state) => client.setQueryData(PERSONALITY_QUERY_KEY, state),
  });
}

export function useResetPersonality() {
  const client = useQueryClient();
  return useMutation<PersonalityState, Error, void>({
    mutationFn: () => request<PersonalityState>("/api/personality", { method: "DELETE" }),
    onSuccess: (state) => client.setQueryData(PERSONALITY_QUERY_KEY, state),
  });
}

export function useMoodAction() {
  const client = useQueryClient();
  return useMutation<PersonalityState, Error, MoodAction>({
    mutationFn: (body) =>
      request<PersonalityState>("/api/personality/mood", {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify(body),
      }),
    onSuccess: (state) => client.setQueryData(PERSONALITY_QUERY_KEY, state),
  });
}

export async function previewPersonality(profile: Partial<PersonalityProfile>): Promise<string> {
  const out = await request<{ instruction: string }>("/api/personality/preview", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify(profile),
  });
  return out.instruction;
}

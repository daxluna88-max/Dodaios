/**
 * The personality section (issue 17): saved only on Save, the mood says it is simulated and why,
 * mood requests are session-only and never write the profile.
 *
 * Written for the PC checklist (P01, P04, P12); not run when it was written.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/i18n", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/i18n")>()),
  useT: () => (key: string) => key,
}));

import { PersonalitySection } from "@/views/assistant/PersonalitySection";
import type { PersonalityState } from "@/views/assistant/personalityApi";

const STATE: PersonalityState = {
  product: "consumer",
  status: "ok",
  reason: "",
  profile: {
    schema_version: 1,
    mode: "mixed",
    preset: null,
    tone: null,
    formality: null,
    warmth: null,
    humor: null,
    concision: null,
    expressive_range: 1,
    moods_allowed: ["neutral", "cheerful", "calm", "focused", "serious"],
    locked_mood: null,
  },
  effective: { mode: "mixed", preset: "friendly", tone: "friendly", formality: 1, warmth: 3, humor: 2, concision: 1 },
  presets: {
    professional: { tone: "neutral", formality: 3, warmth: 1, humor: 0, concision: 3 },
    friendly: { tone: "friendly", formality: 1, warmth: 3, humor: 2, concision: 1 },
    concise: { tone: "neutral", formality: 2, warmth: 1, humor: 0, concision: 4 },
  },
  default_preset: "friendly",
  mood: { mood: "neutral", intensity: 0, origin: "none", reason: "default", expires_at: null, adaptation_on: true },
  mood_available: true,
  simulated: true,
  tones: ["neutral", "friendly", "calm", "energetic", "serious"],
  moods: ["neutral", "cheerful", "calm", "focused", "serious"],
};

function serve(state: PersonalityState = STATE) {
  const calls: Array<{ url: string; method: string; body: Record<string, unknown> | null }> = [];
  (globalThis as unknown as { fetch: typeof fetch }).fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null;
    calls.push({ url: String(url), method: init?.method ?? "GET", body });
    if (String(url).endsWith("/preview")) return { ok: true, status: 200, json: async () => ({ instruction: "Speaking style: x" }) } as Response;
    if (init?.method === "POST") {
      return { ok: true, status: 200, json: async () => ({ ...state, mood: { ...state.mood, mood: "serious", intensity: 2, origin: "owner", reason: "owner_request" } }) } as Response;
    }
    return { ok: true, status: 200, json: async () => state } as Response;
  }) as unknown as typeof fetch;
  return calls;
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <PersonalitySection />
    </QueryClientProvider>,
  );
}

afterEach(() => cleanup());

describe("PersonalitySection", () => {
  it("says the mood is simulated and shows the current one with its reason", async () => {
    serve();
    mount();
    expect(await screen.findByTestId("personality-mood")).toBeTruthy();
    expect(screen.getByText("assistant_view.personality_simulated")).toBeTruthy();
    expect(screen.getByTestId("personality-mood-now").textContent).toContain("assistant_view.personality_reason_default");
  });

  it("does not save anything until Save, and Save sends only what the owner set", async () => {
    const calls = serve();
    mount();
    const save = (await screen.findByTestId("personality-save")) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.click(screen.getByTestId("personality-mode-manual"));
    expect(save.disabled).toBe(false);
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(0);
    fireEvent.click(save);
    await waitFor(() => expect(calls.filter((c) => c.method === "PUT")).toHaveLength(1));
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.body?.mode).toBe("manual");
    expect(put.body?.humor).toBeNull(); // not touched: still follows the preset
    expect(put.body?.moods_allowed).toEqual(STATE.profile.moods_allowed); // kept, not reset
    expect(put.body?.expressive_range).toBe(1);
  });

  it("a mood request goes to the session endpoint and never writes the profile", async () => {
    const calls = serve();
    mount();
    fireEvent.click(await screen.findByTestId("personality-set-serious"));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/mood"))).toBe(true));
    expect(calls.find((c) => c.url.endsWith("/mood"))?.body).toEqual({ action: "set", mood: "serious" });
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(0);
    expect(await screen.findByText(/personality_mood_serious/)).toBeTruthy();
  });

  it("mood buttons are disabled while the assistant is starting", async () => {
    serve({ ...STATE, mood_available: false });
    mount();
    const hold = (await screen.findByTestId("personality-hold")) as HTMLButtonElement;
    expect(hold.disabled).toBe(true);
  });

  it("warns when the saved personality could not be read", async () => {
    serve({ ...STATE, status: "defaults", reason: "humor: too big" });
    mount();
    expect((await screen.findByTestId("personality-status")).textContent).toContain("personality_status_defaults");
  });

  it("preview shows the text and saves nothing", async () => {
    const calls = serve();
    mount();
    fireEvent.click(await screen.findByTestId("personality-preview"));
    expect((await screen.findByTestId("personality-preview-text")).textContent).toContain("Speaking style");
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(0);
  });
});

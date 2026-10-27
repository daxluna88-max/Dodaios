/**
 * The control strip (ADR-0058): STOP is always there and one click, the state is the server's,
 * "Stopped" is never invented, a local engine is not presented as "nothing leaves the computer".
 *
 * Written for the PC checklist (S01, S09, S10, S12, O11); not run when it was written.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/i18n", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/i18n")>()),
  useT: () => (key: string) => key,
}));

import { ControlStrip } from "@/components/ControlStrip";
import type { ControlStrip as Strip } from "@/lib/controlStrip";

const BASE: Strip = {
  state: "idle",
  not_confirmed: [],
  door_open: true,
  generation: 0,
  indicators: { audio: "idle", jobs: 0, approvals: 0 },
  engine: {
    turn_id: "t1",
    inference: "local",
    engines: [{ provider: "ollama", role: "main", locality: "local" }],
    network_tools: "not_observed",
    planned: [],
    attempted: [],
    observability: "declared",
    refused: [],
  },
  jobs: { missions: [], computer_use_runs: 0, background_agents: 0 },
  approvals: [],
  stop: null,
};

function serve(get: Strip, afterStop?: Strip) {
  const calls: Array<{ url: string; method: string }> = [];
  (globalThis as unknown as { fetch: typeof fetch }).fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(url), method: init?.method ?? "GET" });
    const body = String(url).endsWith("/api/stop") && afterStop ? afterStop : get;
    return { ok: true, status: 200, json: async () => body } as Response;
  }) as unknown as typeof fetch;
  return calls;
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ControlStrip />
    </QueryClientProvider>,
  );
}

afterEach(() => cleanup());

describe("ControlStrip", () => {
  it("renders STOP first, even before the state could be read", () => {
    (globalThis as unknown as { fetch: typeof fetch }).fetch = vi.fn(() => new Promise(() => {})) as unknown as typeof fetch;
    mount();
    const buttons = screen.getAllByRole("button");
    expect(buttons[0]).toBe(screen.getByTestId("control-strip-stop"));
    expect(screen.getByTestId("control-strip-state").textContent).toBe("control_strip.state_unknown");
  });

  it("STOP is one click, with no confirmation, and shows the server's partial result", async () => {
    const stopped: Strip = {
      ...BASE,
      state: "partly_stopped",
      door_open: false,
      generation: 1,
      not_confirmed: ["voice_playback"],
      stop: {
        generation: 1,
        state: "partly_stopped",
        repeated: false,
        results: [{ name: "voice_playback", outcome: "unknown", detail: "no_probe" }],
      },
    };
    const calls = serve(BASE, stopped);
    mount();
    await screen.findByText("control_strip.state_idle");
    fireEvent.click(screen.getByTestId("control-strip-stop"));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/stop" && c.method === "POST")).toBe(true));
    expect(await screen.findByText("control_strip.state_partly_stopped")).toBeTruthy();
    // Never the word "Stopped" while something is not confirmed.
    expect(screen.queryByText("control_strip.state_stopped")).toBeNull();
    expect(screen.getByText(/control_strip\.not_confirmed|control_strip\.part_voice_playback/)).toBeTruthy();
    expect(screen.getByTestId("control-strip-resume")).toBeTruthy();
  });

  it("Resume appears only when the door is closed and is its own button", async () => {
    const calls = serve({ ...BASE, state: "stopped", door_open: false });
    mount();
    const resume = await screen.findByTestId("control-strip-resume");
    fireEvent.click(resume);
    await waitFor(() => expect(calls.some((c) => c.url === "/api/stop/resume" && c.method === "POST")).toBe(true));
  });

  it("a local engine never carries a 'nothing leaves' promise: network and tools are separate", async () => {
    serve(BASE);
    mount();
    await screen.findByText("control_strip.state_idle");
    fireEvent.click(screen.getByText("control_strip.details_show"));
    expect(screen.getByText("control_strip.network_not_observed")).toBeTruthy();
    expect(screen.getByText("control_strip.observability_declared")).toBeTruthy();
  });

  it("decisions can be approved or denied one at a time, nothing in bulk", async () => {
    const withDecision: Strip = {
      ...BASE,
      state: "waiting",
      indicators: { audio: "idle", jobs: 1, approvals: 1 },
      approvals: [
        { trace_id: "tr-1", mission_id: "m-1", tool_name: "write_file", risk_tier: "monitor", reason: "", args_preview: "a.txt" },
      ],
    };
    const calls = serve(withDecision);
    mount();
    await screen.findByText("control_strip.state_waiting");
    fireEvent.click(screen.getByText("control_strip.details_show"));
    fireEvent.click(screen.getByText("control_strip.deny"));
    await waitFor(() =>
      expect(calls.some((c) => c.url === "/api/missions/m-1/tool-approvals/tr-1/deny" && c.method === "POST")).toBe(true),
    );
    expect(screen.queryByText(/always|bulk/i)).toBeNull();
  });

  it("unknown counts are shown as unknown, not as zero", async () => {
    serve({ ...BASE, state: "unknown", indicators: { audio: "unknown", jobs: null, approvals: null } });
    mount();
    await screen.findByText("control_strip.state_unknown");
    expect(screen.getAllByText("control_strip.level_unknown").length).toBe(3);
  });
});

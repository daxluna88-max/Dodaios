/**
 * Control strip, block 10: no controls on stale data, jobs and decisions, memory, real limits.
 * Written, NOT run (owner order: no test runs, no type-check in this block).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/i18n", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/i18n")>()),
  useT: () => (key: string) => key,
}));
const setActiveSection = vi.fn();
vi.mock("@/store/events", () => ({
  useEventStore: (select: (s: unknown) => unknown) => select({ setActiveSection, pushToast: vi.fn() }),
}));

import { ControlStrip } from "@/components/ControlStrip";
import type { ControlStrip as Strip } from "@/lib/controlStrip";

const BASE: Strip = {
  boot_id: "b",
  seq: 1,
  server_time: 1,
  state: "waiting",
  not_confirmed: [],
  door_open: true,
  generation: 2,
  indicators: { audio: "idle", jobs: 1, approvals: 1 },
  engine: {
    turn_id: "t1", inference: "local", engines: [], network_tools: "not_observed", planned: [],
    attempted: [], observability: "declared", refused: [], state: "answered", requested: null,
    fell_back: false, phase: "completed",
  },
  jobs: {
    missions: [{ id: "m-1", state: "RUNNING", title: "Fix the build", started_ms: 1000, tool: "write_file", engine: null }],
    computer_use_runs: 0,
    background_agents: 0,
  },
  approvals: [
    { trace_id: "tr-1", mission_id: "m-1", tool_name: "write_file", risk_tier: "monitor", reason: "", args_preview: "a.txt", call_fingerprint: "fp-1" },
    { trace_id: "tr-2", mission_id: "m-1", tool_name: "run_shell", risk_tier: "high", reason: "", args_preview: "ls", call_fingerprint: "fp-2" },
  ],
  memory: { learning_enabled: false, count: 12, archive_is_deletion: false, per_note_approval: false, backups_and_indexes_covered: false },
  limits: {
    network_tools: "not_observed", python_guard_enforced: true, os_enforcement_verified: false,
    voice_stop: { voice_playback: "unknown", realtime_session: "unknown" }, turn_phase: "completed", tools_used_observed: false,
  },
  stop: null,
};

function serve(handler: (url: string, init?: RequestInit) => Strip | Error) {
  const calls: Array<{ url: string; method: string; body?: string }> = [];
  (globalThis as unknown as { fetch: typeof fetch }).fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(url), method: init?.method ?? "GET", body: init?.body as string | undefined });
    const out = handler(String(url), init);
    if (out instanceof Error) throw out;
    return { ok: true, status: 200, json: async () => out } as Response;
  }) as unknown as typeof fetch;
  return calls;
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ControlStrip />
    </QueryClientProvider>,
  );
  return client;
}

afterEach(() => {
  cleanup();
  setActiveSection.mockClear();
});

describe("ControlStrip block 10", () => {
  it("each decision carries the call identity and the STOP generation, one call per click", async () => {
    const calls = serve(() => BASE);
    mount();
    await screen.findByText("control_strip.state_waiting");
    fireEvent.click(screen.getAllByText("control_strip.approve")[0]!);
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/tr-1/approve") && c.method === "POST")).toBe(true));
    const post = calls.find((c) => c.url.endsWith("/tr-1/approve"))!;
    expect(JSON.parse(post.body ?? "{}")).toMatchObject({ call_fingerprint: "fp-1", generation: 2 });
    expect(calls.filter((c) => c.method === "POST" && c.url.includes("tool-approvals")).length).toBe(1);
    // No bulk and no "always allow" control exists.
    expect(screen.queryByText(/always|bulk/i)).toBeNull();
  });

  it("an assertive alert announces pending decisions", async () => {
    serve(() => BASE);
    mount();
    await screen.findByText("control_strip.state_waiting");
    const alert = screen.getByTestId("control-strip-alert");
    expect(alert.getAttribute("aria-live")).toBe("assertive");
    expect(alert.textContent).toBe("control_strip.approvals_alert");
  });

  it("after a lost connection the old reading is historical: no Approve, Deny, Cancel or Resume", async () => {
    let n = 0;
    const calls = serve(() => {
      n += 1;
      return n === 1 ? { ...BASE, door_open: false } : new Error("offline");
    });
    const client = mount();
    await screen.findByText("control_strip.state_waiting");
    await client.refetchQueries({ queryKey: ["control-strip"] });
    await waitFor(() => expect(screen.getByTestId("control-strip-state").textContent).toBe("control_strip.state_unknown"));
    expect(screen.queryByText("control_strip.approve")).toBeNull();
    expect(screen.queryByText("control_strip.deny")).toBeNull();
    expect(screen.queryByTestId("control-strip-resume")).toBeNull();
    fireEvent.click(screen.getByText("control_strip.details_show"));
    expect(screen.getByTestId("control-strip-historical")).toBeTruthy();
    expect(screen.queryByText("control_strip.jobs_cancel")).toBeNull();
    // STOP is still there and still works.
    expect(screen.getByTestId("control-strip-stop")).toBeTruthy();
    expect(calls.length).toBeGreaterThan(1);
  });

  it("lists what runs with a two-step per-job cancel", async () => {
    const calls = serve(() => ({ ...BASE, approvals: [], state: "working", indicators: { audio: "idle", jobs: 1, approvals: 0 } }));
    mount();
    await screen.findByText("control_strip.state_working");
    fireEvent.click(screen.getByText("control_strip.details_show"));
    expect(screen.getByText("Fix the build")).toBeTruthy();
    expect(screen.getByText("control_strip.jobs_engine_not_observed", { exact: false })).toBeTruthy();
    fireEvent.click(screen.getByText("control_strip.jobs_cancel"));
    expect(calls.some((c) => c.url.endsWith("/cancel"))).toBe(false); // armed, not sent
    fireEvent.click(screen.getByText("control_strip.jobs_cancel_confirm"));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/missions/m-1/cancel" && c.method === "POST")).toBe(true));
  });

  it("the Memory entry opens the memory view and the panel states the honest limits", async () => {
    serve(() => ({ ...BASE, approvals: [], state: "idle" }));
    mount();
    await screen.findByText("control_strip.state_idle");
    fireEvent.click(screen.getByTestId("control-strip-memory"));
    expect(setActiveSection).toHaveBeenCalledWith("memory");
    fireEvent.click(screen.getByText("control_strip.details_show"));
    const panel = screen.getByTestId("control-strip-memory-panel");
    expect(panel.textContent).toContain("control_strip.memory_learning_off");
    expect(panel.textContent).toContain("12");
    expect(panel.textContent).toContain("control_strip.memory_limit_archive");
    expect(panel.textContent).toContain("control_strip.memory_limit_approval");
    expect(panel.textContent).toContain("control_strip.memory_limit_backups");
  });

  it("shows the real limits from the server and never an 'offline verified' claim", async () => {
    serve(() => ({ ...BASE, approvals: [], state: "idle" }));
    mount();
    await screen.findByText("control_strip.state_idle");
    fireEvent.click(screen.getByText("control_strip.details_show"));
    const limits = screen.getByTestId("control-strip-limits").textContent ?? "";
    expect(limits).toContain("control_strip.limits_network");
    expect(limits).toContain("control_strip.limits_guard_on");
    expect(limits).toContain("control_strip.limits_os_not_verified");
    expect(limits).not.toContain("control_strip.limits_os_verified");
    expect(limits).toContain("control_strip.limits_voice");
  });

  it("unknown guard values are shown as unknown, not as off or verified", async () => {
    serve(() => ({
      ...BASE, approvals: [], state: "idle",
      limits: { ...BASE.limits!, python_guard_enforced: null, os_enforcement_verified: null },
    }));
    mount();
    await screen.findByText("control_strip.state_idle");
    fireEvent.click(screen.getByText("control_strip.details_show"));
    const limits = screen.getByTestId("control-strip-limits").textContent ?? "";
    expect(limits).toContain("control_strip.limits_guard_unknown");
    expect(limits).toContain("control_strip.limits_os_unknown");
  });
});

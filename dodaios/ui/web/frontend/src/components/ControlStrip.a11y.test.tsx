/**
 * Control strip accessibility (block 15, ADR-0063): live regions, order, names, contrast classes.
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

describe("ControlStrip accessibility", () => {
  it("STOP is the first focusable control and is always named and described", async () => {
    serve(() => BASE);
    mount();
    const stop = await screen.findByTestId("control-strip-stop");
    const focusables = screen.getByTestId("control-strip").querySelectorAll("button, a[href], [tabindex]");
    expect(focusables[0]).toBe(stop);
    expect(stop.getAttribute("aria-describedby")).toBe("control-strip-stop-hint");
    expect(document.getElementById("control-strip-stop-hint")?.textContent).toBe("control_strip.stop_hint");
  });

  it("announces a waiting decision once, assertively; the polite region stays silent", async () => {
    serve(() => BASE);
    mount();
    const alert = await screen.findByTestId("control-strip-alert");
    await waitFor(() => expect(alert.textContent).toBe("control_strip.approvals_alert"));
    expect(alert.getAttribute("aria-live")).toBe("assertive");
    const polite = screen.getByTestId("control-strip").querySelector('[role="status"]');
    expect(polite?.getAttribute("aria-live")).toBe("off");
  });

  it("a calm state is polite and the alert region is empty", async () => {
    serve(() => ({ ...BASE, state: "idle", approvals: [], indicators: { audio: "idle", jobs: 0, approvals: 0 } }));
    mount();
    await screen.findByTestId("control-strip-state");
    expect(screen.getByTestId("control-strip-alert").textContent).toBe("");
    expect(screen.getByTestId("control-strip").querySelector('[role="status"]')?.getAttribute("aria-live")).toBe("polite");
  });

  it("the state word carries the page ink, not the status hue (contrast, 1.4.3)", async () => {
    serve(() => ({ ...BASE, state: "error" }));
    mount();
    const badge = await screen.findByTestId("control-strip-state");
    expect(badge.className).toContain("text-foreground");
    expect(badge.className).not.toContain("text-destructive");
  });

  it("every decision button names the tool it decides", async () => {
    serve(() => BASE);
    mount();
    expect((await screen.findAllByLabelText("control_strip.approve_label")).length).toBe(2);
    const labels = screen.getAllByRole("button").map((b) => b.getAttribute("aria-label")).filter(Boolean);
    expect(labels.length).toBeGreaterThanOrEqual(4);
  });

  it("the details toggle exposes aria-expanded and only points at the panel while it exists", async () => {
    serve(() => BASE);
    mount();
    const toggle = await screen.findByText("control_strip.details_show");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.getAttribute("aria-controls")).toBeNull();
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(toggle.getAttribute("aria-controls")).toBe("control-strip-details");
  });
});

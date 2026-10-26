/** The fan-out screen: explicit, one ceiling, honest about what was and was not asked. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("@/i18n", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/i18n")>()),
  useT: () => (key: string) => key,
}));

import { ConsumerFanoutGroup } from "@/views/settings/ConsumerFanoutGroup";
import type { FanoutStatus } from "@/lib/consumerFanout";

const STATUS: FanoutStatus = {
  available: true,
  reason: "",
  providers: [
    { id: "ollama", kind: "local", local: true, authenticated: true },
    { id: "claude-api", kind: "api", local: false, authenticated: true },
    { id: "openai", kind: "api", local: false, authenticated: false },
  ],
};

function serve(status: FanoutStatus, answer?: object) {
  const posts: Array<Record<string, unknown>> = [];
  (globalThis as unknown as { fetch: typeof fetch }).fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    if (String(url).endsWith("/status")) return { ok: true, status: 200, json: async () => status } as Response;
    posts.push(JSON.parse(String(init?.body)));
    return { ok: true, status: 200, json: async () => answer ?? {} } as Response;
  }) as unknown as typeof fetch;
  return posts;
}

afterEach(() => cleanup());

describe("ConsumerFanoutGroup", () => {
  it("renders nothing when fan-out is not available (Work product)", async () => {
    serve({ available: false, reason: "work_local_only", providers: [] });
    const { container } = render(<ConsumerFanoutGroup />);
    await waitFor(() => expect(container.innerHTML).toBe(""));
  });

  it("cannot tick a disconnected engine and needs a question plus an engine before Ask works", async () => {
    serve(STATUS);
    render(<ConsumerFanoutGroup />);
    const openai = (await screen.findByTestId("fanout-engine-openai")) as HTMLInputElement;
    expect(openai.disabled).toBe(true);
    const run = screen.getByTestId("fanout-run") as HTMLButtonElement;
    expect(run.disabled).toBe(true);
    fireEvent.change(screen.getByTestId("fanout-task"), { target: { value: "Plan a trip" } });
    expect(run.disabled).toBe(true); // still no engine
    fireEvent.click(screen.getByTestId("fanout-engine-ollama"));
    expect(run.disabled).toBe(false);
  });

  it("sends only the question, the ticked engines, one ceiling and the unknown-price choice", async () => {
    const posts = serve(STATUS, {
      branches: [
        { branch: "b1", provider: "ollama", status: "ok", text: "A local answer", reason: "" },
        { branch: "b2", provider: "claude-api", status: "refused", text: "", reason: "aggregate budget would be exceeded or the price is unknown" },
      ],
      report: { budget_reserved: 0, budget_limit: 2, branches: [{ branch: "b1", provider: "ollama", status: "ok", cost_basis: "local_resources", first_token_s: 0.1, total_s: 0.4 }] },
      egress: [],
    });
    render(<ConsumerFanoutGroup />);
    fireEvent.change(await screen.findByTestId("fanout-task"), { target: { value: "Plan a trip" } });
    fireEvent.click(screen.getByTestId("fanout-engine-ollama"));
    fireEvent.click(screen.getByTestId("fanout-engine-claude-api"));
    fireEvent.change(screen.getByTestId("fanout-budget"), { target: { value: "2" } });
    fireEvent.click(screen.getByTestId("fanout-run"));
    await screen.findByTestId("fanout-results");
    expect(posts).toHaveLength(1);
    expect(posts[0]).toEqual({
      task: "Plan a trip",
      budget: 2,
      allow_unknown_cost: false,
      // the API engine has no estimate: null, never a made-up zero; the local engine is free
      branches: [{ provider: "ollama", est_cost: 0 }, { provider: "claude-api", est_cost: null }],
    });
    expect(screen.getByText("A local answer")).toBeTruthy();
    expect(screen.getByTestId("fanout-status-claude-api").textContent).toBe("settings_view.fanout.status_refused");
    expect(screen.getByText("aggregate budget would be exceeded or the price is unknown")).toBeTruthy();
  });

  it("passes a typed estimate through as a number and the unknown-price switch as asked", async () => {
    const posts = serve(STATUS, { branches: [], report: { budget_reserved: 0.5, budget_limit: 1, branches: [] }, egress: [] });
    render(<ConsumerFanoutGroup />);
    fireEvent.change(await screen.findByTestId("fanout-task"), { target: { value: "q" } });
    fireEvent.click(screen.getByTestId("fanout-engine-claude-api"));
    fireEvent.change(screen.getByTestId("fanout-cost-claude-api"), { target: { value: "0,5" } });
    fireEvent.click(screen.getByTestId("fanout-allow-unknown"));
    fireEvent.click(screen.getByTestId("fanout-run"));
    await screen.findByTestId("fanout-results");
    expect(posts[0]).toMatchObject({ allow_unknown_cost: true, branches: [{ provider: "claude-api", est_cost: 0.5 }] });
  });

  it("refuses an invalid ceiling and shows a server error", async () => {
    (globalThis as unknown as { fetch: typeof fetch }).fetch = vi.fn(async (url: RequestInfo | URL) => {
      if (String(url).endsWith("/status")) return { ok: true, status: 200, json: async () => STATUS } as Response;
      return { ok: false, status: 403, json: async () => ({ detail: "not for Work" }) } as Response;
    }) as unknown as typeof fetch;
    render(<ConsumerFanoutGroup />);
    fireEvent.change(await screen.findByTestId("fanout-task"), { target: { value: "q" } });
    fireEvent.click(screen.getByTestId("fanout-engine-ollama"));
    fireEvent.change(screen.getByTestId("fanout-budget"), { target: { value: "lots" } });
    expect((screen.getByTestId("fanout-run") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByTestId("fanout-budget"), { target: { value: "1" } });
    fireEvent.click(screen.getByTestId("fanout-run"));
    await waitFor(() => expect(screen.getByTestId("fanout-error")).toBeTruthy());
  });
});

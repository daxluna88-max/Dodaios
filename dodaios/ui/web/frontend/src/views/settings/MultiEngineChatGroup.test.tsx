/** The chat's "ask several engines" settings: off by default, honest, keeps what it does not edit. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("@/i18n", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/i18n")>()),
  useT: () => (key: string) => key,
}));

import { MultiEngineChatGroup } from "@/views/settings/MultiEngineChatGroup";

const STATUS = {
  available: true,
  reason: "",
  providers: [
    { id: "ollama", kind: "local", local: true, authenticated: true },
    { id: "claude-api", kind: "api", local: false, authenticated: true },
    { id: "openai", kind: "api", local: false, authenticated: false },
  ],
};

const OFF = { available: true, reason: "", enabled: false, engines: [], budget: 0, allow_unknown_cost: false, max_concurrent: 3 };

function serve(saved: object) {
  const puts: Array<Record<string, any>> = [];
  (globalThis as unknown as { fetch: typeof fetch }).fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    if (String(url).endsWith("/status")) return { ok: true, status: 200, json: async () => STATUS } as Response;
    if (init?.method === "PUT") {
      puts.push(JSON.parse(String(init.body)));
      return { ok: true, status: 200, json: async () => saved } as Response;
    }
    return { ok: true, status: 200, json: async () => saved } as Response;
  }) as unknown as typeof fetch;
  return puts;
}

afterEach(() => cleanup());

describe("MultiEngineChatGroup", () => {
  it("renders nothing when the backend says it is not available (Work product)", async () => {
    serve({ available: false, reason: "work_local_only" });
    const { container } = render(<MultiEngineChatGroup />);
    await waitFor(() => expect(container.innerHTML).toBe(""));
  });

  it("starts off, with a zero budget, and cannot tick a disconnected engine", async () => {
    serve(OFF);
    render(<MultiEngineChatGroup />);
    const openai = (await screen.findByTestId("multi-engine-engine-openai")) as HTMLInputElement;
    expect(openai.disabled).toBe(true);
    expect((screen.getByTestId("multi-engine-budget") as HTMLInputElement).value).toBe("0");
  });

  it("will not save 'on' without an engine, and a bad budget blocks saving", async () => {
    serve(OFF);
    render(<MultiEngineChatGroup />);
    const save = (await screen.findByTestId("multi-engine-save")) as HTMLButtonElement;
    fireEvent.click(screen.getByTestId("multi-engine-enabled"));
    expect(save.disabled).toBe(true); // on, but no engine
    fireEvent.click(screen.getByTestId("multi-engine-engine-ollama"));
    expect(save.disabled).toBe(false);
    fireEvent.change(screen.getByTestId("multi-engine-budget"), { target: { value: "abc" } });
    expect(save.disabled).toBe(true);
  });

  it("saves the picked engines: local costs 0, remote only the estimate given, one budget", async () => {
    const puts = serve(OFF);
    render(<MultiEngineChatGroup />);
    await screen.findByTestId("multi-engine-save");
    fireEvent.click(screen.getByTestId("multi-engine-enabled"));
    fireEvent.click(screen.getByTestId("multi-engine-engine-ollama"));
    fireEvent.click(screen.getByTestId("multi-engine-engine-claude-api"));
    fireEvent.change(screen.getByTestId("multi-engine-cost-claude-api"), { target: { value: "0,5" } });
    fireEvent.change(screen.getByTestId("multi-engine-budget"), { target: { value: "2" } });
    fireEvent.click(screen.getByTestId("multi-engine-synth-claude-api"));
    fireEvent.click(screen.getByTestId("multi-engine-save"));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0].enabled).toBe(true);
    expect(puts[0].budget).toBe(2);
    expect(puts[0].engines).toEqual([
      { provider: "ollama", est_cost: 0 },
      { provider: "claude-api", est_cost: 0.5 },
    ]);
    expect(puts[0].synthesizer.provider).toBe("claude-api");
    expect(await screen.findByTestId("multi-engine-message")).toBeTruthy();
  });

  it("keeps the model and timeout of a saved engine the screen does not edit", async () => {
    const puts = serve({
      ...OFF,
      enabled: true,
      budget: 1,
      engines: [{ provider: "claude-api", model: "m-1", timeout_s: 90, est_cost: 0.3 }],
    });
    render(<MultiEngineChatGroup />);
    fireEvent.click(await screen.findByTestId("multi-engine-save"));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0].engines[0]).toEqual({ provider: "claude-api", model: "m-1", timeout_s: 90, est_cost: 0.3 });
  });
});

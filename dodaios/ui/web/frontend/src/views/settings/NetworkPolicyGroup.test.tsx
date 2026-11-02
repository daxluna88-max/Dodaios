/** The strict offline switch: it never claims protection the running process does not give. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("@/i18n", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/i18n")>()),
  useT: () => (key: string) => key,
}));

import { NetworkPolicyGroup } from "@/views/settings/NetworkPolicyGroup";
import type { NetworkPolicyStatus } from "@/lib/networkPolicy";

const OFF: NetworkPolicyStatus = {
  configured: "off", env_override: null, active: false, source: "none", blocked: 0, recent: [],
  restart_required: false,
};

function serve(initial: NetworkPolicyStatus) {
  let current = initial;
  const calls: Array<{ method: string; body?: string }> = [];
  (globalThis as unknown as { fetch: typeof fetch }).fetch = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ method: init?.method ?? "GET", body: init?.body as string | undefined });
    if (init?.method === "PUT") {
      const policy = JSON.parse(String(init.body)).policy as "off" | "local-only";
      current = { ...current, configured: policy, restart_required: (policy === "local-only") !== current.active };
    }
    return { ok: true, status: 200, json: async () => current } as Response;
  }) as unknown as typeof fetch;
  return calls;
}

afterEach(() => cleanup());

describe("NetworkPolicyGroup", () => {
  it("is off by default and says so", async () => {
    serve(OFF);
    render(<NetworkPolicyGroup />);
    expect(await screen.findByTestId("network-policy-inactive")).toBeTruthy();
    expect(screen.getByTestId("network-policy-switch").getAttribute("aria-checked")).toBe("false");
    expect(screen.queryByTestId("network-policy-restart")).toBeNull();
  });

  it("turning it on saves local-only and asks for a restart, without claiming it is active", async () => {
    const calls = serve(OFF);
    render(<NetworkPolicyGroup />);
    fireEvent.click(await screen.findByTestId("network-policy-switch"));
    expect(await screen.findByTestId("network-policy-restart")).toBeTruthy();
    expect(calls.some((c) => c.method === "PUT" && c.body === JSON.stringify({ policy: "local-only" }))).toBe(true);
    expect(screen.queryByTestId("network-policy-active")).toBeNull();
    expect(screen.getByTestId("network-policy-inactive")).toBeTruthy();
  });

  it("shows the running guard, its source and the blocked attempts", async () => {
    serve({ ...OFF, configured: "local-only", active: true, source: "config", blocked: 2, recent: ["203.0.113.7:443"] });
    render(<NetworkPolicyGroup />);
    const note = await screen.findByTestId("network-policy-active");
    expect(note.textContent).toContain("settings_view.network_policy.active");
    expect(screen.getByTestId("network-policy-switch").getAttribute("aria-checked")).toBe("true");
    expect(screen.queryByTestId("network-policy-restart")).toBeNull();
  });

  it("explains an environment override", async () => {
    serve({ ...OFF, env_override: "local-only", restart_required: true });
    render(<NetworkPolicyGroup />);
    expect(await screen.findByTestId("network-policy-env")).toBeTruthy();
  });

  it("shows a save error instead of pretending it worked", async () => {
    (globalThis as unknown as { fetch: typeof fetch }).fetch = vi.fn(async (_u: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PUT") return { ok: false, status: 400, json: async () => ({ detail: "unknown policy" }) } as Response;
      return { ok: true, status: 200, json: async () => OFF } as Response;
    }) as unknown as typeof fetch;
    render(<NetworkPolicyGroup />);
    fireEvent.click(await screen.findByTestId("network-policy-switch"));
    await waitFor(() => expect(screen.getByTestId("network-policy-error")).toBeTruthy());
  });
});

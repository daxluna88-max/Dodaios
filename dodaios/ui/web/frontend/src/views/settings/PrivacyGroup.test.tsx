import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));

import { PrivacyGroup } from "@/views/settings/PrivacyGroup";

const reply = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("PrivacyGroup", () => {
  let calls: { url: string; init?: RequestInit }[];

  function backend(initial: boolean, putStatus = 200) {
    calls = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, init });
        if (init?.method === "PUT") return reply({ allowed: JSON.parse(String(init.body)).allowed }, putStatus);
        return reply({ allowed: initial });
      }),
    );
  }

  beforeEach(() => backend(true));

  it("shows the group title and the switch on when personal context is allowed", async () => {
    render(<PrivacyGroup />);
    expect(screen.getByText("settings_view.privacy_group_title")).toBeDefined();
    const toggle = await screen.findByRole("switch", { name: "settings_view.personal_context_title" });
    await waitFor(() => expect(toggle.getAttribute("aria-checked")).toBe("true"));
  });

  it("shows the switch off when the server says personal context stays local", async () => {
    backend(false);
    render(<PrivacyGroup />);
    const toggle = await screen.findByRole("switch", { name: "settings_view.personal_context_title" });
    await waitFor(() => expect(toggle.getAttribute("aria-checked")).toBe("false"));
  });

  it("sends the new value when toggled", async () => {
    render(<PrivacyGroup />);
    const toggle = await screen.findByRole("switch", { name: "settings_view.personal_context_title" });
    await waitFor(() => expect((toggle as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(toggle);
    await waitFor(() => {
      const put = calls.find((c) => c.init?.method === "PUT");
      expect(put).toBeDefined();
      expect(JSON.parse(String(put!.init!.body))).toEqual({ allowed: false });
    });
  });

  it("puts the switch back and says so when saving fails", async () => {
    backend(true, 500);
    render(<PrivacyGroup />);
    const toggle = await screen.findByRole("switch", { name: "settings_view.personal_context_title" });
    await waitFor(() => expect((toggle as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(toggle);
    await screen.findByRole("alert");
    await waitFor(() => expect(toggle.getAttribute("aria-checked")).toBe("true"));
  });
});

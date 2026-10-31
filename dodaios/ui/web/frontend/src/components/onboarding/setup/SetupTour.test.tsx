import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, expect, it, vi } from "vitest";
import { _resetProvidersCacheForTests } from "@/hooks/useProviders";
import type { useOnboarding } from "@/hooks/useOnboarding";
import { loadLocaleChunk } from "@/i18n";
import en from "@/i18n/locales/onboarding/en.json";
import { useEventStore } from "@/store/events";
import { SetupTour } from "./SetupTour";

type Onb = ReturnType<typeof useOnboarding>;

const openai = {
  id: "openai",
  label: "OpenAI",
  tier: "brain",
  auth_mode: "api_key",
  secret_keys: ["openai_api_key"],
  secrets_set: {} as Record<string, boolean>,
  dashboard_url: null,
  login_cli: null,
  install_hint: null,
  credential_path_hint: null,
  configured: false,
  active: false,
};

let providers = [openai];
let wakeWord = { phrase: "", enabled: false, engine: "auto" };
let cliStatus: Record<string, object> = {};
let loginReplies: Record<string, { status: number; body: unknown }> = {};
let calls: Array<{ url: string; method: string; body?: string }> = [];
// null: the server answers like an older one that has no guided first run (the steps then behave as before).
let firstRun: Record<string, unknown> | null = null;
let applyReply: { status: number; body: unknown } | null = null;

function firstRunState(over: Record<string, unknown> = {}, choices: Record<string, unknown> = {}) {
  return {
    product: "consumer",
    choices: {
      owner_name: null,
      space_name: null,
      interaction: "text",
      personality_skip: false,
      personality_tone: "adapt",
      personality_preset: null,
      memory: "off",
      ...choices,
    },
    applied: false,
    presets: ["friendly", "ironic", "essential"],
    default_preset: "friendly",
    summary: {
      product: "consumer",
      interaction: "text",
      personality: { preset: "friendly", mode: "mixed" },
      engine: {
        provider: "ollama", kind: "local", local: true, installed: true, allowed: true,
        authenticated: true, ready: true, verified: false, reason: "local_server_not_checked",
      },
      memory: { learning_enabled: false },
      actions: { approval_mode: "ask" },
      start: "start",
    },
    ...over,
  };
}

function stubFetch() {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: typeof init?.body === "string" ? init.body : undefined });
      const reply = (body: unknown, status = 200) =>
        Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });
      if (url === "/api/permissions/status") return reply({ platform: "win32" });
      if (url === "/api/onboarding/first-run" && method === "PUT" && firstRun) {
        firstRun = { ...firstRun, choices: JSON.parse(String(init?.body)) };
        return reply(firstRun);
      }
      if (url === "/api/onboarding/first-run/apply" && applyReply) return reply(applyReply.body, applyReply.status);
      if (url === "/api/onboarding/first-run/apply" && firstRun) {
        firstRun = { ...firstRun, applied: true };
        return reply(firstRun);
      }
      if (url === "/api/onboarding/first-run" && method === "GET" && firstRun) return reply(firstRun);
      if (url === "/api/providers") return reply({ providers });
      if (url === "/api/setup/starter-plans") return reply({ plans: [], selected: null, custom_id: "custom" });
      if (url === "/api/settings/wake-word" && method === "PUT") {
        const sent = JSON.parse(String(init?.body)) as { phrase: string };
        wakeWord = { ...wakeWord, phrase: sent.phrase };
        return reply({ ok: true, phrase: sent.phrase, engine: "auto", resolved_engine: "auto", degraded: false });
      }
      if (url === "/api/settings/wake-word") return reply(wakeWord);
      if (url === "/api/settings/keybinds" && method === "PUT") {
        const sent = JSON.parse(String(init?.body)) as { hotkey: string };
        return reply({ ok: true, action: "call", hotkey: sent.hotkey, persisted: true, restart_required: false });
      }
      if (url === "/api/settings/keybinds") return reply({ keybinds: { call: "ctrl+alt+j", dictate: "f8" }, defaults: {}, suggestions: [] });
      if (url === "/api/settings/autostart" && method === "PUT") return reply({ ok: true, enabled: true, supported: true });
      if (url === "/api/settings/autostart") return reply({ enabled: false, supported: true });
      if (url.endsWith("/status") && url in cliStatus) return reply(cliStatus[url]);
      if (url.endsWith("/status")) return reply({ installed: true, connected: false, mode: "unknown" });
      if (url.endsWith("/login") && url in loginReplies) return reply(loginReplies[url].body, loginReplies[url].status);
      return reply({ ok: true });
    }),
  );
}

function fakeOnb(over: Partial<NonNullable<Onb["state"]>> = {}): Onb {
  return {
    state: {
      completed: false,
      current_step: null,
      skipped_steps: [],
      terms: { accepted: false, accepted_version: null, current_version: "1.0" },
      wake_word_acknowledged: false,
      tour_completed: false,
      legal_references: [],
      steps: [],
      ...over,
    },
    loading: false,
    error: null,
    refetch: vi.fn(async () => undefined),
    saveStep: vi.fn(async () => undefined),
    acceptTerms: vi.fn(async () => undefined),
    acknowledgeWakeWord: vi.fn(async () => undefined),
    complete: vi.fn(async () => undefined),
    completeTour: vi.fn(async () => undefined),
  };
}

const step = () => screen.getByTestId("setup-card").dataset.step;

beforeAll(async () => {
  await loadLocaleChunk("onboarding");
});

beforeEach(() => {
  providers = [{ ...openai, secrets_set: {} }];
  wakeWord = { phrase: "", enabled: false, engine: "auto" };
  cliStatus = {};
  loginReplies = {};
  firstRun = null;
  applyReply = null;
  _resetProvidersCacheForTests();
  window.sessionStorage.clear();
  useEventStore.getState().setActiveSection("chats");
  stubFetch();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("opens the setup window on the name, with six step pills", async () => {
  render(<SetupTour onb={fakeOnb()} preview={false} onFinished={vi.fn()} />);
  await screen.findByTestId("setup-name-input");
  expect(step()).toBe("name");
  const pills = within(screen.getByTestId("setup-steps")).getAllByRole("button");
  expect(pills).toHaveLength(6);
  expect(screen.getByTestId("setup-pill-name").getAttribute("aria-current")).toBe("step");
});

it("asks for a name, saves it as the wake word and moves on to how you talk", async () => {
  const onb = fakeOnb();
  render(<SetupTour onb={onb} preview={false} onFinished={vi.fn()} />);
  const input = (await screen.findByTestId("setup-name-input")) as HTMLInputElement;
  const go = screen.getByTestId("onboarding-primary") as HTMLButtonElement;
  expect(go.disabled).toBe(true);
  fireEvent.change(input, { target: { value: "Nova" } });
  await act(async () => {
    fireEvent.click(go);
  });
  await waitFor(() => expect(step()).toBe("voice"));
  const put = calls.find((c) => c.url === "/api/settings/wake-word" && c.method === "PUT");
  expect(JSON.parse(put!.body!)).toMatchObject({ phrase: "Nova", persist: true });
  expect(onb.saveStep).toHaveBeenCalledWith("voice", []);
});

it("keeps a name that did not change without saving it again", async () => {
  wakeWord = { phrase: "Atlas", enabled: true, engine: "auto" };
  render(<SetupTour onb={fakeOnb()} preview={false} onFinished={vi.fn()} />);
  await waitFor(() => expect((screen.getByTestId("setup-name-input") as HTMLInputElement).value).toBe("Atlas"));
  await act(async () => {
    fireEvent.click(screen.getByTestId("onboarding-primary"));
  });
  await waitFor(() => expect(step()).toBe("voice"));
  expect(calls.some((c) => c.url === "/api/settings/wake-word" && c.method === "PUT")).toBe(false);
});

it("lists one row per provider, marks a signed-in one as ready and records a skipped step", async () => {
  cliStatus["/api/claude/status"] = { installed: true, connected: true, mode: "subscription", user_email: "a@b.c" };
  const onb = fakeOnb({ current_step: "connect" });
  render(<SetupTour onb={onb} preview={false} onFinished={vi.fn()} />);
  for (const id of ["claude", "openai", "google", "grok"]) await screen.findByTestId(`setup-sub-${id}`);
  await waitFor(() => expect(screen.getByTestId("setup-sub-claude").dataset.state).toBe("ready"));
  expect(screen.getByTestId("setup-sub-claude").textContent).toContain("a@b.c");
  // Something is connected: Continue moves on without a skip.
  await act(async () => {
    fireEvent.click(screen.getByTestId("onboarding-primary"));
  });
  await waitFor(() => expect(step()).toBe("privacy"));
  expect(onb.saveStep).toHaveBeenLastCalledWith("privacy", []);
});

it("lets the user go on with nothing connected, recorded as skipped", async () => {
  const onb = fakeOnb({ current_step: "connect" });
  render(<SetupTour onb={onb} preview={false} onFinished={vi.fn()} />);
  await screen.findByTestId("setup-connect-nothing");
  await act(async () => {
    fireEvent.click(screen.getByTestId("onboarding-primary"));
  });
  await waitFor(() => expect(step()).toBe("privacy"));
  expect(onb.saveStep).toHaveBeenLastCalledWith("privacy", ["connect"]);
});

it("shows the install command when a subscription's app is missing", async () => {
  cliStatus["/api/codex/status"] = { installed: false, connected: false, mode: "missing" };
  loginReplies["/api/codex/login"] = {
    status: 409,
    body: { detail: { message: "Codex CLI is not installed", install_command: "npm i -g @openai/codex" } },
  };
  render(<SetupTour onb={fakeOnb({ current_step: "connect" })} preview={false} onFinished={vi.fn()} />);
  const connect = await screen.findByTestId("setup-sub-openai-connect");
  await act(async () => {
    fireEvent.click(connect);
  });
  const install = await screen.findByTestId("setup-sub-openai-install");
  expect(install.textContent).toContain("npm i -g @openai/codex");
  expect(calls.some((c) => c.url === "/api/codex/login" && c.method === "POST")).toBe(true);
});

it("takes an API key right in the provider's row", async () => {
  render(<SetupTour onb={fakeOnb({ current_step: "connect" })} preview={false} onFinished={vi.fn()} />);
  fireEvent.click(await screen.findByTestId("setup-sub-openai-key"));
  await screen.findByTestId("setup-key-panel-openai");
  // A row whose family has no key card offers only the sign-in.
  expect(screen.queryByTestId("setup-sub-claude-key")).toBeNull();
});

it("shows the voice settings when the server has no guided first run", async () => {
  wakeWord = { phrase: "Nova", enabled: false, engine: "auto" };
  render(<SetupTour onb={fakeOnb({ current_step: "voice" })} preview={false} onFinished={vi.fn()} />);
  await waitFor(() => expect(screen.getByTestId("setup-voice-wake").textContent).toContain("Hey Nova"));
  await screen.findByTestId("onboarding-autostart");
  expect(screen.getByTestId("setup-voice-keys").textContent).toBe("CtrlAltJ");
});

it("ends the window on the summary and walks the app, then finishes", async () => {
  firstRun = firstRunState({ applied: true });
  const onb = fakeOnb({ current_step: "summary" });
  const onFinished = vi.fn();
  render(<SetupTour onb={onb} preview={false} onFinished={onFinished} />);
  await screen.findByTestId("setup-summary");
  await act(async () => {
    fireEvent.click(screen.getByTestId("onboarding-start"));
  });
  await waitFor(() => expect(step()).toBe("tour"));
  expect(onb.saveStep).toHaveBeenLastCalledWith("tour", []);
  // The walk: chat first, then on through every stop to the end.
  expect(screen.getByTestId("setup-card").dataset.stop).toBe("chat");
  const seen: string[] = [];
  for (let i = 0; i < 12 && !onFinished.mock.calls.length; i++) {
    seen.push(screen.getByTestId("setup-card").dataset.stop ?? "");
    await act(async () => {
      fireEvent.click(screen.getByTestId("walk-next"));
    });
  }
  expect(seen).toEqual(["chat", "voice", "agents", "ide", "plugins", "wake", "done"]);
  expect(onFinished).toHaveBeenCalledTimes(1);
  expect(useEventStore.getState().activeSection).toBe("chats");
});

it("sets the Call shortcut by pressing it", async () => {
  render(<SetupTour onb={fakeOnb({ current_step: "voice" })} preview={false} onFinished={vi.fn()} />);
  await waitFor(() => expect(screen.getByTestId("setup-voice-keys").textContent).toBe("CtrlAltJ"));
  fireEvent.click(screen.getByTestId("setup-voice-call-field"));
  expect(screen.getByTestId("setup-voice-call-field").dataset.keybindRecording).toBe("true");
  await act(async () => {
    fireEvent.keyDown(window, { key: "Control", code: "ControlLeft", ctrlKey: true });
    fireEvent.keyDown(window, { key: "k", code: "KeyK", ctrlKey: true });
    fireEvent.keyUp(window, { key: "k", code: "KeyK", ctrlKey: true });
    fireEvent.keyUp(window, { key: "Control", code: "ControlLeft" });
  });
  await waitFor(() => expect(calls.some((c) => c.url === "/api/settings/keybinds" && c.method === "PUT")).toBe(true));
  const put = calls.find((c) => c.url === "/api/settings/keybinds" && c.method === "PUT");
  expect(JSON.parse(put!.body!)).toMatchObject({ action: "call", hotkey: "ctrl+k" });
});

it("refuses a Call shortcut another shortcut already owns", async () => {
  render(<SetupTour onb={fakeOnb({ current_step: "voice" })} preview={false} onFinished={vi.fn()} />);
  await screen.findByTestId("setup-voice-keys");
  fireEvent.click(screen.getByTestId("setup-voice-call-field"));
  await act(async () => {
    fireEvent.keyDown(window, { key: "F8", code: "F8" });
    fireEvent.keyUp(window, { key: "F8", code: "F8" });
  });
  await new Promise((r) => setTimeout(r, 20));
  expect(calls.some((c) => c.url === "/api/settings/keybinds" && c.method === "PUT")).toBe(false);
});

it("lets the walk be skipped", async () => {
  const onFinished = vi.fn();
  render(<SetupTour onb={fakeOnb({ current_step: "tour" })} preview={false} onFinished={onFinished} />);
  fireEvent.click(await screen.findByTestId("walk-skip"));
  expect(onFinished).toHaveBeenCalledTimes(1);
});

it("goes back to a finished step from its pill", async () => {
  render(<SetupTour onb={fakeOnb({ current_step: "voice" })} preview={false} onFinished={vi.fn()} />);
  await screen.findByTestId("setup-voice-keys");
  fireEvent.click(screen.getByTestId("setup-pill-name"));
  await waitFor(() => expect(step()).toBe("name"));
});

it("never writes the onboarding state in a replay", async () => {
  wakeWord = { phrase: "Nova", enabled: true, engine: "auto" };
  const onb = fakeOnb({ completed: true, current_step: "tour" });
  render(<SetupTour onb={onb} preview onFinished={vi.fn()} />);
  // A replay starts at the beginning, not at the saved step.
  await waitFor(() => expect((screen.getByTestId("setup-name-input") as HTMLInputElement).value).toBe("Nova"));
  await act(async () => {
    fireEvent.click(screen.getByTestId("onboarding-primary"));
  });
  await waitFor(() => expect(step()).toBe("voice"));
  expect(onb.saveStep).not.toHaveBeenCalled();
});

it("sends nothing to the first-run endpoints in a replay, so an installed app's settings stay as they are", async () => {
  firstRun = firstRunState();
  render(<SetupTour onb={fakeOnb({ completed: true })} preview onFinished={vi.fn()} />);
  // A replay starts at the first step ("name"); walk to the interaction step the way a person does.
  fireEvent.change(await screen.findByTestId("setup-name-input"), { target: { value: "Nova" } });
  await act(async () => {
    fireEvent.click(screen.getByTestId("onboarding-primary"));
  });
  await waitFor(() => expect(step()).toBe("voice"));
  fireEvent.click(await screen.findByTestId("setup-interaction-voice"));
  expect(calls.filter((c) => c.url.startsWith("/api/onboarding/first-run") && c.method !== "GET")).toHaveLength(0);
});

it("offers text, voice or both, defaults to text and shows the voice settings only when voice is chosen", async () => {
  firstRun = firstRunState();
  render(<SetupTour onb={fakeOnb({ current_step: "voice" })} preview={false} onFinished={vi.fn()} />);
  const text = (await screen.findByTestId("setup-interaction-text")) as HTMLInputElement;
  expect(text.checked).toBe(true);
  expect(screen.queryByTestId("setup-voice-keys")).toBeNull();
  fireEvent.click(screen.getByTestId("setup-interaction-voice"));
  await screen.findByTestId("setup-voice-keys");
  const put = calls.find((c) => c.url === "/api/onboarding/first-run" && c.method === "PUT");
  expect(JSON.parse(put!.body!)).toMatchObject({ interaction: "voice" });
});

it("saves a personality choice, and skipping it is recorded and keeps the product default", async () => {
  firstRun = firstRunState();
  const onb = fakeOnb({ current_step: "personality" });
  render(<SetupTour onb={onb} preview={false} onFinished={vi.fn()} />);
  fireEvent.click(await screen.findByTestId("setup-personality-ironic"));
  fireEvent.click(screen.getByTestId("setup-personality-tone-keep"));
  await waitFor(() => {
    const last = calls.filter((c) => c.url === "/api/onboarding/first-run" && c.method === "PUT").pop();
    expect(JSON.parse(last!.body!)).toMatchObject({
      personality_preset: "ironic", personality_tone: "keep", personality_skip: false,
    });
  });
  await act(async () => {
    fireEvent.click(screen.getByTestId("setup-personality-skip"));
  });
  await waitFor(() => expect(step()).toBe("connect"));
  expect(onb.saveStep).toHaveBeenLastCalledWith("connect", ["personality"]);
  const last = calls.filter((c) => c.url === "/api/onboarding/first-run" && c.method === "PUT").pop();
  expect(JSON.parse(last!.body!)).toMatchObject({ personality_skip: true });
});

it("Work shows local engines only: no provider rows and no cloud sign-in", async () => {
  firstRun = firstRunState({ product: "work", presets: ["professional", "collaborative", "essential"] });
  render(<SetupTour onb={fakeOnb({ current_step: "connect" })} preview={false} onFinished={vi.fn()} />);
  await screen.findByTestId("setup-work-engine");
  expect(screen.queryByTestId("setup-sub-openai")).toBeNull();
  expect(screen.queryByTestId("setup-engine-notes")).toBeNull();
});

it("Consumer explains the three kinds of engine under the connect step", async () => {
  firstRun = firstRunState();
  render(<SetupTour onb={fakeOnb({ current_step: "connect" })} preview={false} onFinished={vi.fn()} />);
  await screen.findByTestId("setup-engine-notes");
});

it("privacy: memory is off by default, and a failed write keeps the owner on the step with the reason", async () => {
  firstRun = firstRunState();
  applyReply = { status: 500, body: { detail: { error: "first_run_not_applied", step: "approval", written: [] } } };
  const onb = fakeOnb({ current_step: "privacy" });
  render(<SetupTour onb={onb} preview={false} onFinished={vi.fn()} />);
  const memory = await screen.findByTestId("setup-memory-switch");
  expect(memory.getAttribute("aria-checked")).toBe("false");
  await act(async () => {
    fireEvent.click(screen.getByTestId("onboarding-primary"));
  });
  expect((await screen.findByTestId("setup-apply-error")).textContent).toContain("approval");
  expect(step()).toBe("privacy");
  expect(onb.saveStep).not.toHaveBeenCalledWith("summary", expect.anything());
});

it("privacy: applying moves on to the summary", async () => {
  firstRun = firstRunState();
  const onb = fakeOnb({ current_step: "privacy" });
  render(<SetupTour onb={onb} preview={false} onFinished={vi.fn()} />);
  await screen.findByTestId("setup-memory-switch");
  await act(async () => {
    fireEvent.click(screen.getByTestId("onboarding-primary"));
  });
  await waitFor(() => expect(step()).toBe("summary"));
  expect(calls.some((c) => c.url === "/api/onboarding/first-run/apply" && c.method === "POST")).toBe(true);
});

it("the summary says the engine is not verified and offers to explore when no engine is ready", async () => {
  const state = firstRunState();
  (state.summary as Record<string, unknown>).start = "explore";
  (state.summary as { engine: Record<string, unknown> }).engine = {
    provider: "claude-api", kind: "api", local: false, installed: true, allowed: true,
    authenticated: false, ready: false, verified: false, reason: "not_connected",
  };
  firstRun = state;
  render(<SetupTour onb={fakeOnb({ current_step: "summary" })} preview={false} onFinished={vi.fn()} />);
  const start = await screen.findByTestId("onboarding-start");
  expect(start.textContent).toContain(en.first_run.summary.explore);
  expect(screen.getByTestId("setup-summary").textContent).toContain(en.first_run.summary.engine_state_not_connected);
  expect(screen.getByTestId("setup-summary-ready").textContent).toBe(en.first_run.summary.not_ready);
});

it("the summary of a ready engine says it is not verified with a request", async () => {
  firstRun = firstRunState();
  render(<SetupTour onb={fakeOnb({ current_step: "summary" })} preview={false} onFinished={vi.fn()} />);
  const text = (await screen.findByTestId("setup-summary")).textContent ?? "";
  expect(text).toContain(en.first_run.summary.engine_state_local_server_not_checked);
  expect((await screen.findByTestId("onboarding-start")).textContent).toContain(en.first_run.summary.start);
});

it("Modify choices goes back to the first step without losing them", async () => {
  firstRun = firstRunState({}, { owner_name: "Dax" });
  render(<SetupTour onb={fakeOnb({ current_step: "summary" })} preview={false} onFinished={vi.fn()} />);
  fireEvent.click(await screen.findByTestId("setup-edit"));
  await waitFor(() => expect(step()).toBe("name"));
  expect((await screen.findByTestId("setup-owner-name") as HTMLInputElement).value).toBe("Dax");
});

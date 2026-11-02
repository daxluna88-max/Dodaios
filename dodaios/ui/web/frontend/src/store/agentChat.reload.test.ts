import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createAgentChatStore } from "./agentChat";

const reply = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
const row = (id: string) => ({ session_id: id, surface: "jarvis", title: id, provider: "p", model: "m" });
const KEY = "jarvis.agentChat.active.jarvis.v1";

class Socket {
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onopen: (() => void) | null = null;
  close() {}
}

function backend(ids: string[]) {
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    if (String(input).startsWith("/api/agent-chat/sessions")) return reply({ sessions: ids.map(row) });
    return reply({ providers: [], selection: null, default_cwd: "", shell: "test", mapping: [] });
  }));
}

// Every store opened in a test is closed after it, so its socket listeners never
// leak into the next test.
const opened: ReturnType<typeof createAgentChatStore>[] = [];
const makeStore = (surface: Parameters<typeof createAgentChatStore>[0]) => {
  const store = createAgentChatStore(surface);
  opened.push(store);
  return store;
};

beforeEach(() => { localStorage.clear(); vi.stubGlobal("WebSocket", Socket); });
afterEach(() => {
  while (opened.length) opened.pop()!.getState().newChat();
  vi.unstubAllGlobals();
});

describe("chat on stage survives a reload", () => {
  it("reopens the remembered chat once the list shows it", async () => {
    backend(["a", "b"]);
    localStorage.setItem(KEY, "b");
    const store = makeStore("jarvis");
    await store.getState().loadSessions();
    expect(store.getState().activeSessionId).toBe("b");
  });

  it("stays blank when the remembered chat no longer exists", async () => {
    backend(["a"]);
    localStorage.setItem(KEY, "gone");
    const store = makeStore("jarvis");
    await store.getState().loadSessions();
    expect(store.getState().activeSessionId).toBeNull();
  });

  it("remembers what is opened and forgets it on a new chat", async () => {
    backend(["a"]);
    const store = makeStore("jarvis");
    await store.getState().loadSessions();
    store.getState().openSession("a");
    expect(localStorage.getItem(KEY)).toBe("a");
    store.getState().newChat();
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it("a hand-started new chat is not overridden by a late restore", async () => {
    backend(["a"]);
    localStorage.setItem(KEY, "a");
    const store = makeStore("jarvis");
    store.getState().newChat();
    await store.getState().loadSessions();
    expect(store.getState().activeSessionId).toBeNull();
  });

  it("other surfaces never restore", async () => {
    backend(["a"]);
    localStorage.setItem("jarvis.agentChat.active.agent.v1", "a");
    const store = makeStore("agent");
    await store.getState().loadSessions();
    expect(store.getState().activeSessionId).toBeNull();
  });
});

describe("a chat reconnects when the tab comes back", () => {
  class Tracked {
    static all: Tracked[] = [];
    static OPEN = 1;
    readyState = 1;
    onmessage: ((event: { data: string }) => void) | null = null;
    onclose: (() => void) | null = null;
    onopen: (() => void) | null = null;
    onerror: (() => void) | null = null;
    constructor(public url: string) { Tracked.all.push(this); }
    close() { this.readyState = 3; }
  }

  const setVisibility = (state: "hidden" | "visible") => {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
    document.dispatchEvent(new Event("visibilitychange"));
  };

  beforeEach(() => {
    Tracked.all = [];
    vi.stubGlobal("WebSocket", Tracked);
    vi.useFakeTimers();
  });
  afterEach(() => { vi.useRealTimers(); });

  it("re-opens the socket after a long stay in the background, even if it reads open", async () => {
    backend(["a"]);
    const store = makeStore("jarvis");
    await store.getState().loadSessions();
    store.getState().openSession("a");
    expect(Tracked.all).toHaveLength(1);
    setVisibility("hidden");
    vi.advanceTimersByTime(30_000);
    setVisibility("visible");
    expect(Tracked.all).toHaveLength(2);
    expect(Tracked.all[1].url).toContain("a");
  });

  it("leaves a healthy socket alone after a short blink", async () => {
    backend(["a"]);
    const store = makeStore("jarvis");
    await store.getState().loadSessions();
    store.getState().openSession("a");
    setVisibility("hidden");
    vi.advanceTimersByTime(2_000);
    setVisibility("visible");
    expect(Tracked.all).toHaveLength(1);
  });

  it("reconnects when the network returns", async () => {
    backend(["a"]);
    const store = makeStore("jarvis");
    await store.getState().loadSessions();
    store.getState().openSession("a");
    window.dispatchEvent(new Event("online"));
    expect(Tracked.all).toHaveLength(2);
  });
});

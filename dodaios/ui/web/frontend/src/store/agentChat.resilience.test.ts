import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createAgentChatStore } from "./agentChat";

const reply = (data: unknown) => new Response(JSON.stringify(data), { status: 200 });
const row = (id: string) => ({ session_id: id, surface: "jarvis", title: id, provider: "p", model: "m" });
const KEY = "jarvis.agentChat.active.jarvis.v1";

const sockets: Socket[] = [];
class Socket {
  static CONNECTING = 0;
  static OPEN = 1;
  readyState = 0;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onopen: (() => void) | null = null;
  constructor(public url: string) { sockets.push(this); }
  close() {}
  open() { this.readyState = 1; this.onopen?.(); }
  send(frame: unknown) { this.onmessage?.({ data: JSON.stringify(frame) }); }
}

let releaseList: (() => void) | null = null;
function slowBackend(ids: string[]) {
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    if (String(input).startsWith("/api/agent-chat/sessions")) {
      await new Promise<void>((resolve) => { releaseList = resolve; });
      return reply({ sessions: ids.map(row) });
    }
    return reply({ providers: [], selection: null, default_cwd: "", shell: "test", mapping: [] });
  }));
}

const opened: ReturnType<typeof createAgentChatStore>[] = [];
const makeStore = () => {
  const store = createAgentChatStore("jarvis");
  opened.push(store);
  return store;
};

beforeEach(() => {
  localStorage.clear();
  sockets.length = 0;
  vi.stubGlobal("WebSocket", Socket);
});
afterEach(() => {
  while (opened.length) opened.pop()!.getState().newChat();
  vi.unstubAllGlobals();
});

describe("late restore never overrides the person", () => {
  it("a draft edit made while the list loads wins over the restore", async () => {
    slowBackend(["a"]);
    localStorage.setItem(KEY, "a");
    const store = makeStore();
    const loading = store.getState().loadSessions();
    await vi.waitFor(() => expect(releaseList).not.toBeNull());
    await store.getState().setDraft({ cwd: "/tmp" });
    releaseList!();
    await loading;
    expect(store.getState().activeSessionId).toBeNull();
  });

  it("a disconnect while the list loads wins over the restore", async () => {
    slowBackend(["a"]);
    localStorage.setItem(KEY, "a");
    const store = makeStore();
    const loading = store.getState().loadSessions();
    await vi.waitFor(() => expect(releaseList).not.toBeNull());
    store.getState().disconnect();
    releaseList!();
    await loading;
    expect(store.getState().activeSessionId).toBeNull();
  });
});

describe("snapshots belong to the open chat", () => {
  it("ignores a snapshot that names another session", () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply({ sessions: [] })));
    const store = makeStore();
    store.getState().openSession("a");
    sockets[0].open();
    sockets[0].send({ type: "snapshot", session: row("b"), events: [] });
    expect(store.getState().activeSession?.session_id).not.toBe("b");
  });

  it("ignores a snapshot from another surface", () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply({ sessions: [] })));
    const store = makeStore();
    store.getState().openSession("a");
    sockets[0].open();
    sockets[0].send({ type: "snapshot", session: { ...row("a"), surface: "agent" }, events: [] });
    expect(store.getState().activeSession?.surface).not.toBe("agent");
  });

  it("applies the snapshot of the open chat", () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply({ sessions: [] })));
    const store = makeStore();
    store.getState().openSession("a");
    sockets[0].open();
    sockets[0].send({ type: "snapshot", session: row("a"), events: [] });
    expect(store.getState().activeSession?.session_id).toBe("a");
  });
});

describe("online bursts are coalesced", () => {
  it("six online events open one socket", () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply({ sessions: [] })));
    const store = makeStore();
    store.getState().openSession("a");
    sockets[0].open();
    const before = sockets.length;
    for (let i = 0; i < 6; i++) window.dispatchEvent(new Event("online"));
    expect(sockets.length - before).toBe(1);
  });
});

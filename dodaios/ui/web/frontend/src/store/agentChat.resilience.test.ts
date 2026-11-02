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

describe("restore after a failed first list", () => {
  it("does not fire on a later poll once the person edited the draft", async () => {
    let fail = true;
    vi.stubGlobal("fetch", vi.fn(async (input: string) => {
      if (String(input).startsWith("/api/agent-chat/sessions")) {
        if (fail) throw new Error("offline");
        return reply({ sessions: [row("a")] });
      }
      return reply({ providers: [], selection: null, default_cwd: "", shell: "test", mapping: [] });
    }));
    localStorage.setItem(KEY, "a");
    const store = makeStore();
    await store.getState().loadSessions();
    await store.getState().setDraft({ cwd: "/tmp" });
    fail = false;
    await store.getState().loadSessions();
    expect(store.getState().activeSessionId).toBeNull();
  });

  it("still restores on a later poll when the person did nothing", async () => {
    let fail = true;
    vi.stubGlobal("fetch", vi.fn(async (input: string) => {
      if (String(input).startsWith("/api/agent-chat/sessions")) {
        if (fail) throw new Error("offline");
        return reply({ sessions: [row("a")] });
      }
      return reply({ providers: [], selection: null, default_cwd: "", shell: "test", mapping: [] });
    }));
    localStorage.setItem(KEY, "a");
    const store = makeStore();
    await store.getState().loadSessions();
    fail = false;
    await store.getState().loadSessions();
    expect(store.getState().activeSessionId).toBe("a");
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

describe("a turn the session reports idle is closed", () => {
  it("settles a running turn when the snapshot says running=false", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply({ sessions: [] })));
    const store = makeStore();
    store.getState().openSession("a");
    sockets[0].open();
    const started = { seq: 1, ts_ms: 1, kind: "turn_started", payload: { turn_id: "t1", provider: "p", model: "m" } };
    sockets[0].send({ type: "snapshot", session: { ...row("a"), running: true }, events: [started] });
    const items = () => store.getState().timeline.items.filter((i) => i.type === "turn");
    expect(items().map((i) => (i as { status: string }).status)).toEqual(["running"]);
    // The resync after a lock screen reconnects from seq 1 and the terminal
    // event never arrives; only the session's own flag says the turn is over.
    window.dispatchEvent(new Event("online"));
    const again = sockets[sockets.length - 1];
    expect(again.url).toContain("after=1");
    again.open();
    again.send({ type: "snapshot", session: { ...row("a"), running: false }, events: [] });
    expect(items().map((i) => (i as { status: string }).status)).toEqual(["done"]);
  });
});

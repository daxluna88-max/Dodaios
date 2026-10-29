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

beforeEach(() => { localStorage.clear(); vi.stubGlobal("WebSocket", Socket); });
afterEach(() => { vi.unstubAllGlobals(); });

describe("chat on stage survives a reload", () => {
  it("reopens the remembered chat once the list shows it", async () => {
    backend(["a", "b"]);
    localStorage.setItem(KEY, "b");
    const store = createAgentChatStore("jarvis");
    await store.getState().loadSessions();
    expect(store.getState().activeSessionId).toBe("b");
  });

  it("stays blank when the remembered chat no longer exists", async () => {
    backend(["a"]);
    localStorage.setItem(KEY, "gone");
    const store = createAgentChatStore("jarvis");
    await store.getState().loadSessions();
    expect(store.getState().activeSessionId).toBeNull();
  });

  it("remembers what is opened and forgets it on a new chat", async () => {
    backend(["a"]);
    const store = createAgentChatStore("jarvis");
    await store.getState().loadSessions();
    store.getState().openSession("a");
    expect(localStorage.getItem(KEY)).toBe("a");
    store.getState().newChat();
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it("a hand-started new chat is not overridden by a late restore", async () => {
    backend(["a"]);
    localStorage.setItem(KEY, "a");
    const store = createAgentChatStore("jarvis");
    store.getState().newChat();
    await store.getState().loadSessions();
    expect(store.getState().activeSessionId).toBeNull();
  });

  it("other surfaces never restore", async () => {
    backend(["a"]);
    localStorage.setItem("jarvis.agentChat.active.agent.v1", "a");
    const store = createAgentChatStore("agent");
    await store.getState().loadSessions();
    expect(store.getState().activeSessionId).toBeNull();
  });
});

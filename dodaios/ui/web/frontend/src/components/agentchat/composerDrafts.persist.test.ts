import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createAgentChatStore } from "@/store/agentChat";
import {
  composerDraftsFor, flushComposerDrafts, PERSIST_KEY, PERSIST_TTL_MS, readComposerDraft, writeComposerDraft,
} from "./composerDrafts";

/** Unsent text of the front page chat survives a reload; nothing else does. */

const stores = () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }) as Response));
  return { page: createAgentChatStore("jarvis"), other: createAgentChatStore("agent", "threads") };
};

describe("composer drafts across a reload", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it("keeps the typed text of the front page for a new page load", () => {
    const { page } = stores();
    writeComposerDraft(page, null, { text: "ricorda che uso un iPhone", choices: [] });
    writeComposerDraft(page, "s-1", { text: "seconda bozza", choices: [] });
    flushComposerDrafts(page);

    const { page: reloaded } = stores(); // a fresh store object = a reloaded page
    expect(readComposerDraft(reloaded, null).text).toBe("ricorda che uso un iPhone");
    expect(readComposerDraft(reloaded, "s-1").text).toBe("seconda bozza");
  });

  it("writes after a short delay without flushing, and never on every keystroke", () => {
    const { page } = stores();
    const set = vi.spyOn(Storage.prototype, "setItem");
    for (const text of ["c", "ci", "cia", "ciao"]) writeComposerDraft(page, null, { text, choices: [] });
    expect(set).not.toHaveBeenCalled();
    vi.advanceTimersByTime(300);
    expect(set).toHaveBeenCalledTimes(1);
    set.mockRestore();
  });

  it("forgets a draft that was sent or cleared", () => {
    const { page } = stores();
    writeComposerDraft(page, null, { text: "da inviare", choices: [] });
    flushComposerDrafts(page);
    expect(window.localStorage.getItem(PERSIST_KEY)).not.toBeNull();
    writeComposerDraft(page, null, { text: "", choices: [] }); // what the composer does on send
    flushComposerDrafts(page);
    expect(window.localStorage.getItem(PERSIST_KEY)).toBeNull();
    expect(readComposerDraft(stores().page, null).text).toBe("");
  });

  it("does not persist other surfaces (IDE panes, groups, side chats)", () => {
    const { other } = stores();
    writeComposerDraft(other, null, { text: "bozza di un agente", choices: [] });
    flushComposerDrafts(other);
    expect(window.localStorage.getItem(PERSIST_KEY)).toBeNull();
  });

  it("keeps only text, not tool chips", () => {
    const { page } = stores();
    writeComposerDraft(page, null, { text: "con un chip", choices: [{ id: "x" } as never] });
    flushComposerDrafts(page);
    const reloaded = readComposerDraft(stores().page, null);
    expect(reloaded.text).toBe("con un chip");
    expect(reloaded.choices).toEqual([]);
  });

  it("drops drafts older than a week and survives corrupted or unavailable storage", () => {
    window.localStorage.setItem(PERSIST_KEY, JSON.stringify({ savedAt: Date.now() - PERSIST_TTL_MS - 1000, texts: { "": "vecchia" } }));
    expect(readComposerDraft(stores().page, null).text).toBe("");

    window.localStorage.setItem(PERSIST_KEY, "{not json");
    expect(readComposerDraft(stores().page, null).text).toBe("");

    const { page } = stores();
    const boom = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("quota"); });
    expect(() => { writeComposerDraft(page, null, { text: "ok in memoria", choices: [] }); flushComposerDrafts(page); }).not.toThrow();
    expect(composerDraftsFor(page).getState().drafts.get(null)?.text).toBe("ok in memoria");
    boom.mockRestore();
  });
});

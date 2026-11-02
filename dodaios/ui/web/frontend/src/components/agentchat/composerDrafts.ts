import { createStore, useStore } from "zustand";

import type { AgentChatStoreHook } from "@/store/agentChat";
import type { ToolChoice } from "./toolChoices";

export interface MessageDraft {
  text: string;
  choices: ToolChoice[];
}

const EMPTY_DRAFT: MessageDraft = { text: "", choices: [] };
type DraftMap = Map<string | null, MessageDraft>;
const createDraftStore = (initial: DraftMap = new Map()) =>
  createStore<{ drafts: DraftMap }>(() => ({ drafts: initial }));

// Unsent TEXT of the front page's chat survives a reload (a phone tab is reloaded or discarded often).
// Only the text is kept: tool chips point at live objects. It stays in THIS browser's localStorage and is
// never sent anywhere; entries older than a week are dropped. Other surfaces (IDE panes, society groups,
// side chats) are separate stores with no stable identity, so they keep their drafts in memory only.
export const PERSIST_KEY = "jarvis.agentChat.composerDrafts.v1";
export const PERSIST_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const PERSIST_DELAY_MS = 250;
const persistTimers = new WeakMap<AgentChatStoreHook, ReturnType<typeof setTimeout>>();

const persists = (chat: AgentChatStoreHook): boolean => {
  try {
    return chat.getState().surface === "jarvis";
  } catch {
    return false;
  }
};

interface PersistedDrafts { savedAt: number; texts: Record<string, string> }

const keyOf = (sessionId: string | null) => (sessionId === null ? "" : sessionId);

function loadPersisted(now = Date.now()): DraftMap {
  const out: DraftMap = new Map();
  try {
    const raw = window.localStorage.getItem(PERSIST_KEY);
    if (!raw) return out;
    const data = JSON.parse(raw) as Partial<PersistedDrafts>;
    if (!data || typeof data.savedAt !== "number" || now - data.savedAt > PERSIST_TTL_MS) return out;
    for (const [key, text] of Object.entries(data.texts ?? {})) {
      if (typeof text === "string" && text) out.set(key === "" ? null : key, { text, choices: [] });
    }
  } catch {
    /* unreadable or blocked storage: start with no drafts */
  }
  return out;
}

function savePersisted(chat: AgentChatStoreHook): void {
  try {
    const texts: Record<string, string> = {};
    for (const [sessionId, draft] of composerDraftsFor(chat).getState().drafts) {
      if (draft.text) texts[keyOf(sessionId)] = draft.text;
    }
    if (Object.keys(texts).length === 0) {
      window.localStorage.removeItem(PERSIST_KEY);
      return;
    }
    window.localStorage.setItem(PERSIST_KEY, JSON.stringify({ savedAt: Date.now(), texts } satisfies PersistedDrafts));
  } catch {
    /* quota or blocked storage: the draft stays in memory */
  }
}

function schedulePersist(chat: AgentChatStoreHook): void {
  const pending = persistTimers.get(chat);
  if (pending !== undefined) clearTimeout(pending);
  persistTimers.set(chat, setTimeout(() => {
    persistTimers.delete(chat);
    savePersisted(chat);
  }, PERSIST_DELAY_MS));
}

/** Write any pending draft now (the page is going away). Exported for the tests and the pagehide hook. */
export function flushComposerDrafts(chat: AgentChatStoreHook): void {
  const pending = persistTimers.get(chat);
  if (pending === undefined) return;
  clearTimeout(pending);
  persistTimers.delete(chat);
  savePersisted(chat);
}
// Keep unsent text in memory, owned by its chat store, across view unmounts.
// Separate stores also isolate the front page from IDE panes and other agents.
const stores = new WeakMap<AgentChatStoreHook, ReturnType<typeof createDraftStore>>();
export interface ComposerDraftTarget { sessionId: string | null; superseded?: boolean }
const blankTargets = new WeakMap<AgentChatStoreHook, ComposerDraftTarget>();

/** Capture before sending so a late failure restores the chat that was created. */
export function captureComposerDraftTarget(chat: AgentChatStoreHook, sessionId: string | null): ComposerDraftTarget {
  if (sessionId !== null) return { sessionId };
  let target = blankTargets.get(chat);
  if (!target || target.sessionId !== null) {
    target = { sessionId: null };
    blankTargets.set(chat, target);
  }
  return target;
}

export function resetBlankComposerDraftTarget(chat: AgentChatStoreHook): void {
  const previous = blankTargets.get(chat);
  if (previous?.sessionId === null) previous.superseded = true;
  writeComposerDraft(chat, null, EMPTY_DRAFT);
  blankTargets.set(chat, { sessionId: null });
}

/** Transfer unsent text/chips before opening the assigned session. */
export function bindComposerDraftTarget(chat: AgentChatStoreHook, target: ComposerDraftTarget, sessionId: string): boolean {
  const currentBlank = target.sessionId === null && blankTargets.get(chat) === target;
  target.sessionId = sessionId;
  target.superseded = false;
  if (!currentBlank) return false;
  const source = readComposerDraft(chat, null);
  const destination = readComposerDraft(chat, sessionId);
  writeComposerDraft(chat, sessionId, {
    text: [source.text, destination.text].filter(Boolean).join("\n"),
    choices: [...source.choices, ...destination.choices.filter((row) => !source.choices.some((sent) => sent.id === row.id))],
  });
  writeComposerDraft(chat, null, EMPTY_DRAFT);
  return true;
}

export function composerDraftsFor(chat: AgentChatStoreHook) {
  let store = stores.get(chat);
  if (!store) {
    store = createDraftStore(persists(chat) ? loadPersisted() : new Map());
    stores.set(chat, store);
    if (persists(chat) && typeof window !== "undefined") {
      // iOS Safari may kill a background tab without a reload: flush when the page is hidden.
      window.addEventListener("pagehide", () => flushComposerDrafts(chat));
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "hidden") flushComposerDrafts(chat);
      });
    }
  }
  return store;
}

export function readComposerDraft(chat: AgentChatStoreHook, sessionId: string | null): MessageDraft {
  return composerDraftsFor(chat).getState().drafts.get(sessionId) ?? EMPTY_DRAFT;
}

export function writeComposerDraft(chat: AgentChatStoreHook, sessionId: string | null, draft: MessageDraft) {
  const store = composerDraftsFor(chat);
  const previous = readComposerDraft(chat, sessionId);
  if (previous.text === draft.text && JSON.stringify(previous.choices) === JSON.stringify(draft.choices)) return;
  store.setState(({ drafts }) => {
    const next = new Map(drafts);
    if (draft.text || draft.choices.length) next.set(sessionId, draft);
    else next.delete(sessionId);
    return { drafts: next };
  });
  if (persists(chat)) schedulePersist(chat);
}

export function useComposerDraft(chat: AgentChatStoreHook, sessionId: string | null): MessageDraft {
  return useStore(composerDraftsFor(chat), (state) => state.drafts.get(sessionId) ?? EMPTY_DRAFT);
}

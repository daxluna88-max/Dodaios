import { afterEach, describe, expect, it, vi } from "vitest";
vi.mock("@/store/events", () => ({
  useEventStore: Object.assign(() => "Jarvis", { getState: () => ({ assistantName: "Jarvis" }) }),
}));
import {
  hydrateReplyLanguage, hydrateSttLanguage, hydrateUiLanguage, isUiLanguage,
  loadLocaleChunk, loadUiLocale, setReplyLanguage, setSttLanguage, setUiLanguage,
  translate, useI18nStore,
} from "./index";
import { localeForUiLanguage } from "@/components/runs/format";
const ok = (body: unknown) => Promise.resolve({ ok: true, json: async () => body });
afterEach(() => {
  useI18nStore.getState().setUi("en", { push: false });
  useI18nStore.getState().setReply("auto", { push: false });
  useI18nStore.getState().setStt("auto", { push: false });
  localStorage.clear();
  vi.unstubAllGlobals();
});
describe("Italian language integration", () => {
  it("recognizes Italian and loads the dictionary once", async () => {
    expect(isUiLanguage("it")).toBe(true);
    expect(isUiLanguage("it-unsupported")).toBe(false);
    const pending = loadUiLocale("it");
    expect(loadUiLocale("it")).toBe(pending);
    await pending;
    useI18nStore.getState().setUi("it", { push: false });
    expect(translate("nav.settings")).toBe("Impostazioni");
    expect(translate("sidebar.new_chat")).toBe("Nuova conversazione");
    expect(translate("languages_view.options.it.label")).toBe("Italiano");
    expect(document.documentElement.lang).toBe("it");
    expect(localeForUiLanguage("it")).toBe("it-IT");
  });
  it("saves independent UI, reply and recognition choices to their own endpoints", async () => {
    const fetcher = vi.fn((_url: RequestInfo | URL, _init?: RequestInit) => ok({}));
    vi.stubGlobal("fetch", fetcher);
    setUiLanguage("it");
    expect(useI18nStore.getState().reply).toBe("auto");
    expect(useI18nStore.getState().stt).toBe("auto");
    setReplyLanguage("it");
    setSttLanguage("it");
    for (const kind of ["ui", "reply", "stt"]) {
      expect(localStorage.getItem(`jarvis.${kind}.language`)).toBe("it");
      expect(fetcher).toHaveBeenCalledWith(`/api/settings/${kind}-language`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ language: "it" }),
      });
    }
  });
  it("hydrates persisted Italian choices without echo PUT requests", async () => {
    const fetcher = vi.fn((_url: RequestInfo | URL, _init?: RequestInit) => ok({ language: "it", options: ["auto", "en", "it"] }));
    vi.stubGlobal("fetch", fetcher);
    await Promise.all([hydrateUiLanguage(), hydrateReplyLanguage(), hydrateSttLanguage()]);
    const state = useI18nStore.getState();
    expect([state.ui, state.reply, state.stt]).toEqual(["it", "it", "it"]);
    expect(state.sttOptions).toContain("it");
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(fetcher.mock.calls.every((args) => args.length === 1)).toBe(true);
  });
  it("loads and interpolates the Italian first-run guide", async () => {
    await loadLocaleChunk("onboarding");
    useI18nStore.getState().setUi("it", { push: false });
    expect(translate("first_run.window.title")).toBe("Configura Jarvis");
    expect(translate("first_run.continue")).toBe("Continua");
  });
});

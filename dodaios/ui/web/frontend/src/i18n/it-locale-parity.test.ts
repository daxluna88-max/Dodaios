/**
 * The Italian interface is a PARTIAL, UNREVIEWED DRAFT (ADR-0063). Unlike the
 * complete languages, it may omit keys: a missing key falls back to English.
 * What it must never do is carry a key English does not have, drop or invent a
 * `{placeholder}`, hold an empty string, or lose its draft marker.
 *
 * WRITTEN, NOT RUN (no test runner was permitted when this was authored).
 */
import { describe, expect, it } from "vitest";
import en from "./locales/en.json";
import it_ from "./locales/it.json";
import mcEn from "./locales/memory_control/en.json";
import mcIt from "./locales/memory_control/it.json";
import frEn from "./locales/first_run_hardening/en.json";
import frIt from "./locales/first_run_hardening/it.json";
import plEn from "./locales/personality_limits/en.json";
import plIt from "./locales/personality_limits/it.json";
import obEn from "./locales/onboarding/en.json";
import obIt from "./locales/onboarding/it.json";
import { loadLocaleChunk, loadUiLocale, translate, useI18nStore, isUiLanguage } from "./index";

type Tree = Record<string, unknown>;

const PAIRS: [string, Tree, Tree][] = [
  ["main", en, it_],
  ["memory_control", mcEn, mcIt],
  ["first_run_hardening", frEn, frIt],
  ["personality_limits", plEn, plIt],
  ["onboarding", obEn, obIt],
];

const MARKERS = new Set(["_review_status", "_status", "review_status"]);

function flat(tree: Tree, prefix = ""): Map<string, string> {
  const out = new Map<string, string>();
  for (const [k, v] of Object.entries(tree)) {
    if (v && typeof v === "object") {
      for (const [ck, cv] of flat(v as Tree, `${prefix}${k}.`)) out.set(ck, cv);
    } else out.set(`${prefix}${k}`, String(v));
  }
  return out;
}
const tokens = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort();
const leaf = (key: string) => key.split(".").pop() ?? key;

describe("Italian is a registered interface language", () => {
  it("is accepted by isUiLanguage", () => {
    expect(isUiLanguage("it")).toBe(true);
  });
});

describe.each(PAIRS)("it draft: %s", (_name, source, italian) => {
  const src = flat(source);
  const dst = flat(italian);

  it("has no key English lacks (markers excepted)", () => {
    const extra = [...dst.keys()].filter((k) => !src.has(k) && !MARKERS.has(leaf(k)));
    expect(extra).toEqual([]);
  });
  it("has no empty value", () => {
    const empty = [...dst].filter(([, v]) => v.trim() === "").map(([k]) => k);
    expect(empty).toEqual([]);
  });
  it("keeps every placeholder of the English source", () => {
    const bad = [...dst].filter(([k, v]) => src.has(k) && JSON.stringify(tokens(v)) !== JSON.stringify(tokens(src.get(k) as string))).map(([k]) => k);
    expect(bad).toEqual([]);
  });
  it("carries an unreviewed-draft marker", () => {
    expect([...dst.keys()].some((k) => MARKERS.has(leaf(k)))).toBe(true);
  });
});

describe("priority chunks are complete in Italian", () => {
  it.each([
    ["memory_control", mcEn, mcIt],
    ["first_run_hardening", frEn, frIt],
    ["personality_limits", plEn, plIt],
    ["onboarding", obEn, obIt],
  ] as [string, Tree, Tree][])("%s has every English key", (_n, a, b) => {
    const dst = flat(b);
    expect([...flat(a).keys()].filter((k) => !dst.has(k) && !MARKERS.has(leaf(k)))).toEqual([]);
  });
  it("control_strip is complete in the main Italian dictionary", () => {
    const a = flat((en as Tree).control_strip as Tree);
    const b = flat((it_ as Tree).control_strip as Tree);
    expect([...a.keys()].filter((k) => !b.has(k))).toEqual([]);
  });
});

describe("English fallback", () => {
  it("serves an Italian string where one exists and English where it does not", async () => {
    await loadUiLocale("it");
    useI18nStore.getState().setUi("it", { push: false });
    expect(translate("control_strip.stop")).toBe((it_ as any).control_strip.stop);
    // settings_view is not translated yet: the English text must answer, never the key.
    const enText = (en as any).settings_view.network_policy;
    if (typeof enText === "string") expect(translate("settings_view.network_policy")).toBe(enText);
    useI18nStore.getState().setUi("en", { push: false });
  });
  it("loads a chunk that has no Italian file without failing, and answers in English", async () => {
    await loadLocaleChunk("pane_review");
    useI18nStore.getState().setUi("it", { push: false });
    const [[key, text]] = [...flat((await import("./locales/pane_review/en.json")).default as Tree)];
    expect(translate(key)).toBe(text);
    useI18nStore.getState().setUi("en", { push: false });
  });
});

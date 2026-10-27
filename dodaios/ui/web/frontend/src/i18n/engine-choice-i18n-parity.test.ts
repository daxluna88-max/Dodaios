/**
 * The engine-choice surfaces (Network, Ask several engines, the Local voice section and its "Answers from" row)
 * must be complete in every interface language: every key the components ask for exists in all five dictionaries,
 * holds the same placeholders as English and is not left in English. The components are read as source, so a key
 * added without its translations (or put in the wrong dictionary) fails here, not in front of a person.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import en from "./locales/en.json";
import de from "./locales/de.json";
import es from "./locales/es.json";
import pt from "./locales/pt.json";
import zh from "./locales/zh.json";
import pEn from "./locales/providers/en.json";
import pDe from "./locales/providers/de.json";
import pEs from "./locales/providers/es.json";
import pPt from "./locales/providers/pt.json";
import pZh from "./locales/providers/zh.json";

type Tree = Record<string, unknown>;

/** Main dictionary + the lazily loaded providers dictionary, as the app merges them. */
const LANGS: Record<string, Tree[]> = {
  en: [en, pEn],
  de: [de, pDe],
  es: [es, pEs],
  pt: [pt, pPt],
  zh: [zh, pZh],
};

function lookup(trees: Tree[], key: string): unknown {
  for (const tree of trees) {
    let node: unknown = tree;
    for (const part of key.split(".")) {
      if (!node || typeof node !== "object") {
        node = undefined;
        break;
      }
      node = (node as Tree)[part];
    }
    if (typeof node === "string") return node;
  }
  return undefined;
}

const SOURCES = [
  "../views/settings/NetworkPolicyGroup.tsx",
  "../views/settings/ConsumerFanoutGroup.tsx",
  "../views/settings/MultiEngineChatGroup.tsx",
  "../views/apikeys/VoiceEngine.tsx",
  "../components/providers/LocalVoicePanel.tsx",
];

/** Literal keys the sources ask for, plus the dynamic families they build. */
function requiredKeys(): string[] {
  const keys = new Set<string>();
  for (const file of SOURCES) {
    const text = readFileSync(join(__dirname, file), "utf8");
    for (const m of text.matchAll(/t\(\s*"((?:settings_view\.(?:network_policy|fanout|multi_engine)|providers_page\.local_voice_section|apikeys_view\.local_voice_answer)[\w.]*)"/g)) {
      keys.add(m[1]);
    }
  }
  for (const kind of ["local", "subscription", "api"]) keys.add(`settings_view.fanout.kind_${kind}`);
  for (const status of ["ok", "refused", "failed", "timeout", "cancelled", "stale"]) {
    keys.add(`settings_view.fanout.status_${status}`);
  }
  // Ternary keys the regex cannot see.
  keys.add("apikeys_view.local_voice_answer_local");
  keys.add("apikeys_view.local_voice_answer_remote");
  return [...keys].sort();
}

const PLACEHOLDER = /\{\w+\}/g;
const placeholders = (text: unknown) =>
  typeof text === "string" ? (text.match(PLACEHOLDER) ?? []).sort() : [];

describe("engine-choice copy", () => {
  const keys = requiredKeys();

  it("covers the surfaces (the key scan found something)", () => {
    expect(keys.length).toBeGreaterThan(30);
  });

  it.each(Object.keys(LANGS))("%s has every key the components ask for", (lang) => {
    const missing = keys.filter((key) => lookup(LANGS[lang], key) === undefined);
    expect(missing).toEqual([]);
  });

  it.each(Object.keys(LANGS).filter((l) => l !== "en"))("%s keeps English's placeholders and is translated", (lang) => {
    for (const key of keys) {
      const english = lookup(LANGS.en, key);
      const local = lookup(LANGS[lang], key);
      expect(placeholders(local), key).toEqual(placeholders(english));
      if (typeof english === "string" && english.length > 12) expect(local, key).not.toBe(english);
    }
  });
});

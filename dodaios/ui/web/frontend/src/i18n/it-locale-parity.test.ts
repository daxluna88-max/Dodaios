/** Every shipped Italian dictionary must preserve source keys and placeholders. */
import { readFileSync, readdirSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "locales");
function englishFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const file = join(directory, entry.name);
    if (entry.isDirectory()) return englishFiles(file);
    return entry.name === "en.json" || entry.name.endsWith(".en.json") ? [file] : [];
  });
}
function checkTree(source: unknown, target: unknown, path: string): void {
  if (source === null) {
    expect(target, path).toBeNull();
  } else if (Array.isArray(source)) {
    expect(Array.isArray(target), path).toBe(true);
    expect((target as unknown[]).length, path).toBe(source.length);
    source.forEach((value, index) => checkTree(value, (target as unknown[])[index], `${path}[${index}]`));
  } else if (typeof source === "object") {
    expect(target && typeof target === "object", path).toBeTruthy();
    const original = source as Record<string, unknown>;
    const translated = target as Record<string, unknown>;
    expect(Object.keys(translated).sort(), path).toEqual(Object.keys(original).sort());
    for (const key of Object.keys(original)) checkTree(original[key], translated[key], `${path}.${key}`);
  } else {
    expect(typeof target, path).toBe(typeof source);
    if (typeof source === "string") {
      const tokens = (text: string) => (text.match(/\{\w+\}/g) ?? []).sort();
      expect(tokens(target as string), path).toEqual(tokens(source));
      if (source.trim()) expect((target as string).trim(), path).not.toBe("");
    }
  }
}

describe("Italian dictionary parity", () => {
  for (const english of englishFiles(ROOT)) {
    const italian = english.replace(/(^|[/.])en\.json$/, "$1it.json");
    it(`${relative(ROOT, english)} has complete Italian keys, types and placeholders`, () => {
      checkTree(JSON.parse(readFileSync(english, "utf8")), JSON.parse(readFileSync(italian, "utf8")), relative(ROOT, italian));
    });
  }
  it("uses Italian protocol and formatting values", () => {
    const dictionary = JSON.parse(readFileSync(join(ROOT, "it.json"), "utf8"));
    expect(dictionary.slash.locale).toBe("it");
    expect(dictionary.trace_report.locale).toBe("it-IT");
    expect(dictionary.languages_view.options.it.label).toBe("Italiano");
  });
});

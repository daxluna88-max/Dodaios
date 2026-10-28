import { describe, expect, it } from "vitest";
import { initialNavCollapsed } from "./navCollapsedDefault";

describe("initialNavCollapsed", () => {
  it("starts on the rail at phone width when nothing is stored", () => {
    expect(initialNavCollapsed(null, 393)).toBe(true);
  });

  it("starts expanded on a desktop window when nothing is stored", () => {
    expect(initialNavCollapsed(null, 1280)).toBe(false);
  });

  it("honors a stored expanded choice even on a narrow window", () => {
    expect(initialNavCollapsed("false", 393)).toBe(false);
  });

  it("honors a stored rail choice on a wide window", () => {
    expect(initialNavCollapsed("true", 1280)).toBe(true);
  });

  it("treats the 640 px boundary as expanded", () => {
    expect(initialNavCollapsed(null, 640)).toBe(false);
    expect(initialNavCollapsed(null, 639)).toBe(true);
  });
});

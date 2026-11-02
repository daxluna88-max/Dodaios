import { describe, expect, it } from "vitest";
import { micGateAfterUserTurn, micGateOpenAtStart, parseMicMode } from "./micSettings";

describe("micSettings", () => {
  it("defaults to auto for unknown values", () => {
    expect(parseMicMode(null)).toBe("auto");
    expect(parseMicMode("x")).toBe("auto");
    expect(parseMicMode("push")).toBe("push");
    expect(parseMicMode("always")).toBe("always");
  });
  it("push starts closed, the others open", () => {
    expect(micGateOpenAtStart("push")).toBe(false);
    expect(micGateOpenAtStart("auto")).toBe(true);
    expect(micGateOpenAtStart("always")).toBe(true);
  });
  it("only auto closes after the user's turn", () => {
    expect(micGateAfterUserTurn("auto", true)).toBe(false);
    expect(micGateAfterUserTurn("always", true)).toBe(true);
    expect(micGateAfterUserTurn("push", false)).toBe(false);
  });
});

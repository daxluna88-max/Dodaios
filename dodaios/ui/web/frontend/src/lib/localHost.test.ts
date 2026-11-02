import { describe, expect, it } from "vitest";
import { isLocalHost } from "./localHost";

describe("isLocalHost", () => {
  it("accepts loopback names", () => {
    for (const h of ["localhost", "127.0.0.1", "[::1]", "::1", "app.localhost"]) expect(isLocalHost(h)).toBe(true);
  });
  it("rejects a remote or tailnet host", () => {
    for (const h of ["srv.tail11f095.ts.net", "example.com", "192.168.1.5", ""]) expect(isLocalHost(h)).toBe(false);
  });
});

/**
 * Ordering and staleness of the control strip replies (ADR-0058, block 10).
 * Written, NOT run (owner order: no test runs, no type-check in this block).
 */
import { describe, expect, it } from "vitest";
import { QueryClient } from "@tanstack/react-query";

import { isStale, StripOrderer, STALE_AFTER_MS, stripOrderer, viewOf, type ControlStrip } from "@/lib/controlStrip";

function strip(over: Partial<ControlStrip> = {}): ControlStrip {
  return {
    boot_id: "boot-1",
    seq: 1,
    state: "idle",
    not_confirmed: [],
    door_open: true,
    generation: 0,
    indicators: { audio: "idle", jobs: 0, approvals: 0 },
    engine: {
      turn_id: null, inference: "none", engines: [], network_tools: "not_observed", planned: [],
      attempted: [], observability: "none", refused: [], state: "answered", requested: null, fell_back: false,
    },
    jobs: { missions: [], computer_use_runs: 0, background_agents: 0 },
    approvals: [],
    stop: null,
    ...over,
  };
}

describe("StripOrderer", () => {
  it("applies a newer reply and drops an older or repeated one", () => {
    const o = new StripOrderer();
    expect(o.accept(strip({ seq: 5 }), 100)).toBe(true);
    expect(o.accept(strip({ seq: 4, state: "working" }), 200)).toBe(false);
    expect(o.accept(strip({ seq: 5, state: "working" }), 200)).toBe(false);
    expect(o.current?.state).toBe("idle");
    expect(o.lastOkAt).toBe(100); // a dropped reply never refreshes the time of the last good read
    expect(o.accept(strip({ seq: 6, state: "working" }), 300)).toBe(true);
    expect(o.lastOkAt).toBe(300);
  });

  it("a GET sent before STOP cannot repaint the pre-STOP state after the STOP reply", () => {
    const o = new StripOrderer();
    o.accept(strip({ seq: 1, generation: 0, state: "working" }), 100);
    const slow = o.nextRequest(); // the poll that stays in flight
    o.raiseBarrier(); // STOP begins
    expect(o.accept(strip({ seq: 3, generation: 1, state: "stopped", door_open: false }), 150)).toBe(true);
    // The slow poll finally answers with a state built before STOP (lower seq, older generation).
    expect(o.accept(strip({ seq: 2, generation: 0, state: "working" }), 160, slow.clientSeq)).toBe(false);
    expect(o.current?.state).toBe("stopped");
  });

  it("a reply to a request sent before the barrier is dropped even if its seq looks new", () => {
    const o = new StripOrderer();
    const before = o.nextRequest();
    o.raiseBarrier();
    expect(o.accept(strip({ seq: 9 }), 100, before.clientSeq)).toBe(false);
    const after = o.nextRequest();
    expect(o.accept(strip({ seq: 9 }), 100, after.clientSeq)).toBe(true);
  });

  it("never goes back across a STOP generation, even with a higher seq", () => {
    const o = new StripOrderer();
    o.accept(strip({ seq: 1, generation: 2 }), 100);
    expect(o.accept(strip({ seq: 2, generation: 1 }), 110)).toBe(false);
    expect(o.accept(strip({ seq: 3, generation: 2 }), 120)).toBe(true);
  });

  it("a server restart starts a new epoch; a late reply of the old process is dropped", () => {
    const o = new StripOrderer();
    o.accept(strip({ boot_id: "A", seq: 50, generation: 3 }), 100);
    expect(o.accept(strip({ boot_id: "B", seq: 1, generation: 0 }), 200)).toBe(true);
    expect(o.accept(strip({ boot_id: "A", seq: 51, generation: 3 }), 210)).toBe(false);
    expect(o.current?.boot_id).toBe("B");
  });

  it("sends the known STOP generation with the next poll and numbers polls upward", () => {
    const o = new StripOrderer();
    o.accept(strip({ seq: 1, generation: 4 }), 100);
    const a = o.nextRequest();
    const b = o.nextRequest();
    expect(b.clientSeq).toBe(a.clientSeq + 1);
    expect(a.knownGeneration).toBe(4);
  });

  it("an older server without ordering fields still cannot cross a STOP generation backwards", () => {
    const o = new StripOrderer();
    o.accept(strip({ boot_id: undefined, seq: undefined, generation: 1 }), 100);
    expect(o.accept(strip({ boot_id: undefined, seq: undefined, generation: 0 }), 110)).toBe(false);
  });

  it("keeps one orderer per query client", () => {
    const a = new QueryClient();
    expect(stripOrderer(a)).toBe(stripOrderer(a));
    expect(stripOrderer(a)).not.toBe(stripOrderer(new QueryClient()));
  });
});

describe("viewOf: a failed or old read is never current", () => {
  const base = () => {
    const orderer = new StripOrderer();
    orderer.accept(strip({ seq: 1 }), 1000);
    return orderer;
  };
  it("is current right after a good read", () => {
    const v = viewOf({ orderer: base(), isError: false, now: 1000, offline: false });
    expect(v.current?.state).toBe("idle");
    expect(v.historical).toBeUndefined();
  });
  it("after a failed refetch the kept data is historical, not current (React Query keeps it)", () => {
    const v = viewOf({ orderer: base(), isError: true, now: 1001, offline: false });
    expect(v.current).toBeUndefined();
    expect(v.historical?.state).toBe("idle");
    expect(v.lastOkAt).toBe(1000);
  });
  it("older than the threshold, or offline, is historical", () => {
    expect(viewOf({ orderer: base(), isError: false, now: 1000 + STALE_AFTER_MS + 1, offline: false }).current).toBeUndefined();
    expect(viewOf({ orderer: base(), isError: false, now: 1001, offline: true }).current).toBeUndefined();
  });
  it("with nothing read there is neither current nor historical", () => {
    const v = viewOf({ orderer: new StripOrderer(), isError: false, now: 5, offline: false });
    expect(v.current).toBeUndefined();
    expect(v.historical).toBeUndefined();
  });
  it("isStale treats offline as stale", () => {
    expect(isStale({ hasData: true, failed: false, updatedAt: 1, now: 1, offline: true })).toBe(true);
  });
});

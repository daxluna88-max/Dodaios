// Ordering rules of the desktop ControlStrip (frontend/src/lib/controlStrip.ts, StripOrderer),
// ported 1:1 for the phone panel (ADR-0062): drop a reply from an older boot, an older seq, an older
// STOP generation, or one whose request was sent before a STOP began (barrier) or before a resync.
"use strict";

class StripOrderer {
  constructor() {
    this.session = null;
    this.lastSeq = -1;
    this.lastGeneration = -1;
    this.nextClientSeq = 1;
    this.barrier = 0;
    this.epochFloor = 0;
    this.latestWrite = 0;
    this.current = undefined;
    this.lastOkAt = 0;
  }
  nextRequest() { return { clientSeq: this.nextClientSeq++ }; }
  isAfterBarrier(clientSeq) { return clientSeq >= this.barrier; }
  beginWriteRequest() {
    const seq = this.nextClientSeq++;
    this.barrier = seq;
    this.latestWrite = seq;
    return seq;
  }
  isCurrentWrite(seq) { return seq === this.latestWrite; }
  resync() {
    this.session = null;
    this.lastSeq = -1;
    this.lastGeneration = -1;
    this.epochFloor = this.nextClientSeq;
    this.current = undefined;
    this.lastOkAt = 0;
  }
  accept(next, now = Date.now(), clientSeq) {
    if (clientSeq !== undefined && (!this.isAfterBarrier(clientSeq) || clientSeq < this.epochFloor)) return false;
    const boot = next.boot_id;
    const seq = next.seq;
    if (boot !== undefined && seq !== undefined) {
      if (this.session === null) this.session = boot;
      else if (boot !== this.session) { this.resync(); return false; }
      if (seq <= this.lastSeq) return false;
      if (next.generation < this.lastGeneration) return false;
      this.lastSeq = seq;
    } else if (next.generation < this.lastGeneration) {
      return false;
    }
    this.lastGeneration = Math.max(this.lastGeneration, next.generation);
    this.current = next;
    this.lastOkAt = now;
    return true;
  }
}
if (typeof module !== "undefined") module.exports = { StripOrderer };

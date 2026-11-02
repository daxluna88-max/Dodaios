import { useSyncExternalStore } from "react";

/** How the browser microphone gate behaves during a voice call. */
export type MicMode = "auto" | "push" | "always";

const MODE_KEY = "jarvis.mic.mode";
const DEVICE_KEY = "jarvis.mic.deviceId";

export function parseMicMode(value: unknown): MicMode {
  return value === "push" || value === "always" ? value : "auto";
}

/** Storage can throw (private window, blocked site data): never let it break voice. */
export function loadMicMode(): MicMode {
  try {
    return parseMicMode(window.localStorage.getItem(MODE_KEY));
  } catch {
    return "auto";
  }
}

export function saveMicMode(mode: MicMode): void {
  try {
    window.localStorage.setItem(MODE_KEY, mode);
  } catch {
    /* per-viewer convenience only */
  }
}

export function loadMicDeviceId(): string {
  try {
    return window.localStorage.getItem(DEVICE_KEY) ?? "";
  } catch {
    return "";
  }
}

export function saveMicDeviceId(deviceId: string): void {
  try {
    if (deviceId) window.localStorage.setItem(DEVICE_KEY, deviceId);
    else window.localStorage.removeItem(DEVICE_KEY);
  } catch {
    /* per-viewer convenience only */
  }
}

/** Whether the gate starts open when a call begins. Push-to-talk starts closed. */
export function micGateOpenAtStart(mode: MicMode): boolean {
  return mode !== "push";
}

/** Auto mode closes the gate once the person's turn is final; tap reopens it. */
export function micGateAfterUserTurn(mode: MicMode, current: boolean): boolean {
  return mode === "auto" ? false : current;
}

// ---- Shared store -------------------------------------------------------------------------------
// BrowserRealtimeControl owns the call but is mounted `controlOnly` (it paints nothing); the controls
// the person touches live in the voice composer. Both talk through this module, like browserVoiceCall.

export type MicDevice = { id: string; label: string };
export type MicState = { mode: MicMode; deviceId: string; open: boolean; devices: MicDevice[] };

const initialMode = loadMicMode();
let state: MicState = { mode: initialMode, deviceId: loadMicDeviceId(), open: micGateOpenAtStart(initialMode), devices: [] };
const listeners = new Set<() => void>();

function update(patch: Partial<MicState>): void {
  state = { ...state, ...patch };
  listeners.forEach((fn) => fn());
}

export function getMicState(): MicState {
  return state;
}

export function subscribeMic(fn: () => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

export function useMicSettings(): MicState {
  return useSyncExternalStore(subscribeMic, getMicState, getMicState);
}

export function setMicModeSetting(mode: MicMode): void {
  saveMicMode(mode);
  update({ mode, open: micGateOpenAtStart(mode) });
}

export function setMicDeviceSetting(deviceId: string): void {
  saveMicDeviceId(deviceId);
  update({ deviceId });
}

export function setMicGateOpen(open: boolean): void {
  if (state.open !== open) update({ open });
}

/** Called when a call starts: the gate takes the mode's starting position. */
export function resetMicGate(): void {
  setMicGateOpen(micGateOpenAtStart(state.mode));
}

/** The person's turn is final: auto mode closes the gate until they tap. */
export function closeMicGateAfterUserTurn(): void {
  setMicGateOpen(micGateAfterUserTurn(state.mode, state.open));
}

/** Labels are empty until microphone permission is granted; the ids are enough to choose. */
export async function refreshMicDevices(): Promise<void> {
  try {
    const all = await navigator.mediaDevices?.enumerateDevices?.();
    if (!all) return;
    const devices = all
      .filter((d) => d.kind === "audioinput" && d.deviceId && d.deviceId !== "default" && d.deviceId !== "communications")
      .map((d, i) => ({ id: d.deviceId, label: d.label || `#${i + 1}` }));
    update({ devices });
  } catch {
    /* no device list: the picker stays hidden */
  }
}

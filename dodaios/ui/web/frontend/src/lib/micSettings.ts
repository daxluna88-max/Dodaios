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

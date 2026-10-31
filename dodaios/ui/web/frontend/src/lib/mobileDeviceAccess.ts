/**
 * Browser capability boundary for Jarvis. No sensor starts on import.
 * A native iOS/Android shell can implement the same interface later.
 */
export type DeviceInput = "photo" | "video" | "files" | "microphone" | "location" | "motion";
export type DeviceCapability = "available" | "unsupported" | "secure-context-required";
export type LocationFix = { latitude: number; longitude: number; accuracyMeters: number; acquiredAt: number };
export type MediaResult = { files: File[]; source: "photo" | "video" | "files" };

export interface JarvisDeviceAdapter {
  capability(input: DeviceInput): DeviceCapability;
  chooseFiles(input: "photo" | "video" | "files"): Promise<MediaResult | null>;
  location(): Promise<LocationFix>;
  microphone(): Promise<MediaStream>;
  stop(stream: MediaStream): void;
}

/** Single-use input; no automatic upload or persistence. */
export function browserDeviceAdapter(doc: Document = document): JarvisDeviceAdapter {
  function secure(): boolean {
    return typeof window !== "undefined" && window.isSecureContext;
  }
  return {
    capability(input) {
      if (input === "photo" || input === "video" || input === "files") return "available";
      if (!secure()) return "secure-context-required";
      if (input === "location") return navigator.geolocation ? "available" : "unsupported";
      if (input === "microphone") return navigator.mediaDevices?.getUserMedia ? "available" : "unsupported";
      return typeof DeviceMotionEvent !== "undefined" ? "available" : "unsupported";
    },
    async chooseFiles(input) {
      return new Promise((resolve) => {
        const picker = doc.createElement("input");
        picker.type = "file";
        picker.style.display = "none";
        picker.accept = input === "photo" ? "image/*" : input === "video" ? "video/*" : "*/*";
        if (input !== "files") picker.setAttribute("capture", "environment");
        picker.multiple = input === "files";
        let settled = false;
        const done = (files: File[] | null) => {
          if (settled) return;
          settled = true;
          picker.remove();
          resolve(files?.length ? { files, source: input } : null);
        };
        picker.addEventListener("change", () => done(Array.from(picker.files ?? [])), { once: true });
        picker.addEventListener("cancel", () => done(null), { once: true });
        doc.body.appendChild(picker);
        try { picker.click(); } catch { done(null); }
      });
    },
    async location() {
      if (!secure()) throw new Error("CONTESTO_NON_SICURO");
      if (!navigator.geolocation) throw new Error("POSIZIONE_NON_DISPONIBILE");
      return new Promise((resolve, reject) => navigator.geolocation.getCurrentPosition(
        (p) => resolve({
          latitude: p.coords.latitude, longitude: p.coords.longitude,
          accuracyMeters: p.coords.accuracy, acquiredAt: p.timestamp,
        }),
        () => reject(new Error("POSIZIONE_NEGATA_O_NON_DISPONIBILE")),
        { enableHighAccuracy: false, timeout: 15000, maximumAge: 0 },
      ));
    },
    async microphone() {
      if (!secure()) throw new Error("CONTESTO_NON_SICURO");
      if (!navigator.mediaDevices?.getUserMedia) throw new Error("MICROFONO_NON_DISPONIBILE");
      return navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    },
    stop(stream) { stream.getTracks().forEach((track) => track.stop()); },
  };
}

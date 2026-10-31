import { useRef, useState } from "react";
import { Camera, FileUp, MapPin, Mic, Video, X } from "lucide-react";
import {
  browserDeviceAdapter, type JarvisDeviceAdapter, type LocationFix, type MediaResult,
} from "@/lib/mobileDeviceAccess";

export type MobileDeviceMenuProps = {
  /** Parent owns the attachment pipeline; this component never uploads on its own. */
  onMedia: (result: MediaResult) => void;
  /** Coordinates are delivered only after the person taps Posizione and grants access. */
  onLocation: (fix: LocationFix) => void;
  /** Mic stream stays local; parent must wire it to existing Jarvis voice transport. */
  onMicrophone?: (stream: MediaStream) => void;
  adapter?: JarvisDeviceAdapter;
};

export function MobileDeviceMenu({
  onMedia, onLocation, onMicrophone, adapter = browserDeviceAdapter(),
}: MobileDeviceMenuProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const streamRef = useRef<MediaStream | null>(null);
  const tasks = [
    { id: "photo" as const, text: "Fotocamera", Icon: Camera },
    { id: "video" as const, text: "Registra video", Icon: Video },
    { id: "files" as const, text: "File e galleria", Icon: FileUp },
    { id: "location" as const, text: "Posizione", Icon: MapPin },
    { id: "microphone" as const, text: "Microfono", Icon: Mic },
  ];
  async function activate(id: (typeof tasks)[number]["id"]) {
    if (busy) return;
    setError("");
    if (adapter.capability(id) !== "available") {
      setError("Funzione non disponibile: controlla browser, HTTPS e permessi.");
      return;
    }
    setBusy(true);
    try {
      if (id === "location") onLocation(await adapter.location());
      else if (id === "microphone") {
        if (!onMicrophone) throw new Error("COLLEGAMENTO_VOCE_NON_CONFIGURATO");
        const stream = await adapter.microphone();
        streamRef.current = stream;
        try { onMicrophone(stream); } catch (e) { adapter.stop(stream); streamRef.current = null; throw e; }
      } else {
        const files = await adapter.chooseFiles(id);
        if (files) onMedia(files);
      }
      setOpen(false);
    } catch {
      setError("Operazione annullata o non consentita. Nessun dato inviato.");
    } finally { setBusy(false); }
  }
  return (
    <div className="relative" data-testid="jarvis-device-menu">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
        aria-label="Accessori telefono" className="rounded-full border px-3 py-2 text-sm">
        Accessori
      </button>
      {open && <div role="group" aria-label="Accessori telefono"
        className="absolute bottom-full left-0 z-40 mb-2 w-56 rounded-xl border bg-background p-2 shadow-lg">
        {tasks.map(({ id, text, Icon }) => (
          <button key={id} type="button" disabled={busy} onClick={() => void activate(id)}
            className="flex w-full items-center gap-3 rounded-lg p-2 text-left text-sm hover:bg-muted disabled:opacity-50">
            <Icon size={18} aria-hidden="true" />{text}
          </button>
        ))}
        <button type="button" className="flex items-center gap-2 p-2 text-xs"
          onClick={() => { if (streamRef.current) adapter.stop(streamRef.current); streamRef.current = null; setOpen(false); }}>
          <X size={15} /> Chiudi / interrompi microfono
        </button>
      </div>}
      {error && <p role="alert" className="mt-1 text-xs text-destructive">{error}</p>}
    </div>
  );
}

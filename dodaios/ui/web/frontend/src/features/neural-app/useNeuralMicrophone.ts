import { useCallback, useEffect, useRef, useState } from "react";

export type VoiceState = "idle" | "requesting" | "recording" | "stopping" | "error";
/** The recording stays on-device until the host explicitly handles it. */
export function useNeuralMicrophone(onAudio?: (blob: Blob) => Promise<void> | void) {
  const [state, setState] = useState<VoiceState>("idle");
  const [error, setError] = useState("");
  const streamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const generation = useRef(0);
  const mounted = useRef(true);
  const stop = useCallback(() => {
    generation.current++;
    const r = recorderRef.current;
    if (r && r.state !== "inactive") {
      if (mounted.current) setState("stopping");
      r.stop();
    } else if (mounted.current) setState("idle");
    streamRef.current?.getTracks().forEach(t => t.stop());
    streamRef.current = null;
  }, []);
  const start = useCallback(async () => {
    if (recorderRef.current?.state === "recording" || streamRef.current) return;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setError("Registrazione microfono non disponibile in questo browser");
      setState("error"); return;
    }
    const ticket = ++generation.current;
    setError(""); setState("requesting");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!mounted.current || ticket !== generation.current) {
        stream.getTracks().forEach(t=>t.stop()); return;
      }
      streamRef.current = stream;
      chunks.current = [];
      const recorder = new MediaRecorder(stream);
      recorderRef.current = recorder;
      recorder.ondataavailable = e => { if (e.data.size) chunks.current.push(e.data); };
      recorder.onerror = () => { setError("Errore durante la registrazione"); stop(); };
      recorder.onstop = () => {
        stream.getTracks().forEach(t=>t.stop());
        if (streamRef.current === stream) streamRef.current = null;
        if (recorderRef.current === recorder) recorderRef.current = null;
        const audio = new Blob(chunks.current, { type: recorder.mimeType || "audio/webm" });
        chunks.current = [];
        if (mounted.current) setState("idle");
        if (mounted.current && audio.size && onAudio) void Promise.resolve(onAudio(audio)).catch(() => {
          if (mounted.current) { setError("Impossibile elaborare l'audio"); setState("error"); }
        });
      };
      recorder.start(250);
      setState("recording");
    } catch {
      setError("Microfono negato o non accessibile");
      if (mounted.current) setState("error");
    }
  }, [onAudio, stop]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      generation.current++;
      const r = recorderRef.current;
      if (r) { r.onstop = null; r.ondataavailable = null; if (r.state !== "inactive") r.stop(); }
      streamRef.current?.getTracks().forEach(t => t.stop());
      streamRef.current = null; recorderRef.current = null; chunks.current = [];
    };
  }, []);
  return { state, error, start, stop, listening: state === "recording" };
}

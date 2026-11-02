import { useEffect } from "react";
import { Mic, MicOff } from "lucide-react";

import { BrandedSelect } from "@/components/ui/select";
import { useT } from "@/i18n";
import {
  refreshMicDevices, setMicDeviceSetting, setMicGateOpen, setMicModeSetting, useMicSettings, type MicMode,
} from "@/lib/micSettings";
import { cn } from "@/lib/utils";

const SELECT =
  "h-7 max-w-[200px] min-w-0 truncate rounded-md border border-border bg-card px-1.5 text-xs text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

/**
 * Microphone picker and the three ways the microphone behaves during a call:
 * automatic (it closes after your turn; tap to continue), hold to talk, always on.
 * The call itself is owned by BrowserRealtimeControl; both share lib/micSettings.
 */
export function MicControls({ live }: { live: boolean }) {
  const t = useT();
  const { mode, deviceId, open, devices } = useMicSettings();

  useEffect(() => {
    void refreshMicDevices();
  }, []);

  const gate = live && mode !== "always";
  const push = mode === "push";
  return (
    <div className="flex flex-col gap-2" data-testid="mic-controls">
      {gate && (
        <button
          type="button"
          data-testid="mic-gate"
          aria-pressed={open}
          onClick={push ? undefined : () => setMicGateOpen(!open)}
          onPointerDown={push ? (e) => {
            // Capture the pointer: a finger that drifts off the button must not release it.
            try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* not supported */ }
            setMicGateOpen(true);
          } : undefined}
          onPointerUp={push ? () => setMicGateOpen(false) : undefined}
          onPointerCancel={push ? () => setMicGateOpen(false) : undefined}
          onContextMenu={push ? (e) => e.preventDefault() : undefined}
          style={push ? { touchAction: "none", WebkitTouchCallout: "none", WebkitUserSelect: "none" } : undefined}
          className={cn(
            "flex h-14 w-full touch-manipulation select-none items-center justify-center gap-2.5 rounded-2xl text-base font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            open ? "bg-accent-soft text-accent" : "bg-foreground text-background hover:bg-foreground/85",
          )}
        >
          {open ? (
            <Mic className="h-5 w-5 animate-pulse motion-reduce:animate-none" aria-hidden />
          ) : (
            <MicOff className="h-5 w-5" aria-hidden />
          )}
          {push
            ? t(open ? "sidebar.mic_push_active" : "sidebar.mic_push_hold")
            : t(open ? "sidebar.mic_auto_listening" : "sidebar.mic_auto_tap")}
        </button>
      )}
      {live && mode === "always" && (
        <div
          data-testid="mic-always"
          className="flex h-10 w-full items-center justify-center gap-2 rounded-2xl bg-accent-soft text-sm font-medium text-accent"
        >
          <Mic className="h-4 w-4" aria-hidden />
          {t("sidebar.mic_always_on")}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-1.5">
      <BrandedSelect
        ariaLabel={t("sidebar.mic_mode_label")}
        testId="mic-mode"
        value={mode}
        onValueChange={(v) => setMicModeSetting(v as MicMode)}
        options={[
          { value: "auto", label: t("sidebar.mic_mode_auto") },
          { value: "push", label: t("sidebar.mic_mode_push") },
          { value: "always", label: t("sidebar.mic_mode_always") },
        ]}
        className={SELECT}
      />
      {devices.length > 1 && (
        <BrandedSelect
          ariaLabel={t("sidebar.mic_device_label")}
          testId="mic-device"
          value={devices.some((d) => d.id === deviceId) ? deviceId : ""}
          onValueChange={setMicDeviceSetting}
          options={[
            { value: "", label: t("sidebar.mic_device_default") },
            ...devices.map((d) => ({ value: d.id, label: d.label })),
          ]}
          className={SELECT}
        />
      )}
      </div>
    </div>
  );
}

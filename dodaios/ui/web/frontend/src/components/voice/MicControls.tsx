import { useEffect } from "react";

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
    <div className="flex flex-wrap items-center gap-1.5" data-testid="mic-controls">
      {gate && (
        <button
          type="button"
          data-testid="mic-gate"
          aria-pressed={open}
          onClick={push ? undefined : () => setMicGateOpen(!open)}
          onPointerDown={push ? () => setMicGateOpen(true) : undefined}
          onPointerUp={push ? () => setMicGateOpen(false) : undefined}
          onPointerLeave={push ? () => setMicGateOpen(false) : undefined}
          onPointerCancel={push ? () => setMicGateOpen(false) : undefined}
          onContextMenu={push ? (e) => e.preventDefault() : undefined}
          className={cn(
            "h-7 touch-manipulation select-none rounded-full px-3 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            open ? "bg-accent-soft text-accent" : "border border-border text-foreground hover:bg-secondary",
          )}
        >
          {push
            ? t(open ? "sidebar.mic_push_active" : "sidebar.mic_push_hold")
            : t(open ? "sidebar.mic_auto_listening" : "sidebar.mic_auto_tap")}
        </button>
      )}
      <select
        aria-label={t("sidebar.mic_mode_label")}
        data-testid="mic-mode"
        value={mode}
        onChange={(e) => setMicModeSetting(e.target.value as MicMode)}
        className={SELECT}
      >
        <option value="auto">{t("sidebar.mic_mode_auto")}</option>
        <option value="push">{t("sidebar.mic_mode_push")}</option>
        <option value="always">{t("sidebar.mic_mode_always")}</option>
      </select>
      {devices.length > 1 && (
        <select
          aria-label={t("sidebar.mic_device_label")}
          data-testid="mic-device"
          value={devices.some((d) => d.id === deviceId) ? deviceId : ""}
          onChange={(e) => setMicDeviceSetting(e.target.value)}
          className={SELECT}
        >
          <option value="">{t("sidebar.mic_device_default")}</option>
          {devices.map((d) => <option key={d.id} value={d.id}>{d.label}</option>)}
        </select>
      )}
    </div>
  );
}

/**
 * Settings > General > Ask several engines in the chat (ADR-0055).
 *
 * Configures what a chat message such as "ask several engines: ..." does. Off by default; nothing here runs an engine.
 * Only the question leaves this computer, one ceiling covers every engine and the final answer together, an engine
 * without a cost estimate is skipped unless allowed, and an engine that is not connected cannot be ticked. Hidden
 * when the backend says it is not available (the Work product is local only).
 */
import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { fill, useT } from "@/i18n";
import {
  fetchFanoutStatus,
  fetchMultiEngine,
  parseCost,
  saveMultiEngine,
  type FanoutEngine,
  type MultiEngineEngine,
  type MultiEngineSettings,
} from "@/lib/consumerFanout";
import {
  SettingsCard,
  SettingsNote,
  SettingsRow,
  SettingsSection,
} from "@/views/settings/SettingsLayout";

const FIELD =
  "rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground " +
  "placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

/** "" = first engine that answered writes the final answer. */
const FIRST_ANSWERED = "";

export function MultiEngineChatGroup() {
  const t = useT();
  const [available, setAvailable] = useState<boolean | null>(null);
  const [engines, setEngines] = useState<FanoutEngine[]>([]);
  // The saved rows, kept whole so model / timeout the screen does not edit survive a save.
  const [saved, setSaved] = useState<Record<string, MultiEngineEngine>>({});
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [costs, setCosts] = useState<Record<string, string>>({});
  const [enabled, setEnabled] = useState(false);
  const [budget, setBudget] = useState("0");
  const [allowUnknown, setAllowUnknown] = useState(false);
  const [synth, setSynth] = useState(FIRST_ANSWERED);
  const [maxConcurrent, setMaxConcurrent] = useState(3);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([fetchMultiEngine(), fetchFanoutStatus().catch(() => null)])
      .then(([state, status]) => {
        if (cancelled) return;
        setAvailable(state.available);
        if (!state.available) return;
        const rows = Object.fromEntries(state.engines.map((e) => [e.provider, e]));
        setSaved(rows);
        setPicked(Object.fromEntries(state.engines.map((e) => [e.provider, true])));
        setCosts(
          Object.fromEntries(
            state.engines.map((e) => [e.provider, e.est_cost == null ? "" : String(e.est_cost)]),
          ),
        );
        setEnabled(state.enabled);
        setBudget(String(state.budget));
        setAllowUnknown(state.allow_unknown_cost);
        setSynth(state.synthesizer?.provider ?? FIRST_ANSWERED);
        setMaxConcurrent(state.max_concurrent);
        const known = status?.providers ?? [];
        // A saved engine that is no longer installed stays visible so the owner can untick it.
        const missing = state.engines
          .filter((e) => !known.some((k) => k.id === e.provider))
          .map((e): FanoutEngine => ({ id: e.provider, kind: "api", local: false, authenticated: false }));
        setEngines([...known, ...missing]);
      })
      .catch(() => !cancelled && setAvailable(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const chosen = useMemo(() => engines.filter((engine) => picked[engine.id]), [engines, picked]);
  const ceiling = parseCost(budget);

  if (!available) return null;

  const rowFor = (engine: FanoutEngine): MultiEngineEngine => ({
    ...saved[engine.id],
    provider: engine.id,
    // Engines on this computer cost no money; everything else only counts with an estimate.
    est_cost: engine.kind === "local" ? 0 : parseCost(costs[engine.id] ?? ""),
  });

  const save = async () => {
    setBusy(true);
    setMessage(null);
    const synthEngine = chosen.find((engine) => engine.id === synth);
    const body: MultiEngineSettings = {
      enabled,
      engines: chosen.map(rowFor),
      synthesizer: synthEngine ? rowFor(synthEngine) : null,
      budget: ceiling ?? 0,
      allow_unknown_cost: allowUnknown,
      max_concurrent: maxConcurrent,
    };
    try {
      await saveMultiEngine(body);
      setSaved(Object.fromEntries(body.engines.map((e) => [e.provider, e])));
      setMessage({ tone: "ok", text: t("settings_view.multi_engine.saved") });
    } catch (err) {
      setMessage({
        tone: "error",
        text: fill(t("settings_view.multi_engine.error"), { detail: err instanceof Error ? err.message : String(err) }),
      });
    } finally {
      setBusy(false);
    }
  };

  const canSave = !busy && ceiling !== null && (!enabled || chosen.length > 0);

  return (
    <SettingsSection
      title={t("settings_view.multi_engine.section_title")}
      description={t("settings_view.multi_engine.section_desc")}
    >
      <SettingsCard>
        <SettingsRow testId="multi-engine-form" title={t("settings_view.multi_engine.enable_label")}>
          <label className="flex items-center gap-2 text-sm">
            <Switch
              checked={enabled}
              onCheckedChange={setEnabled}
              aria-label={t("settings_view.multi_engine.enable_label")}
              data-testid="multi-engine-enabled"
            />
            <span className="text-muted-foreground">{t("settings_view.multi_engine.enable_hint")}</span>
          </label>
          <fieldset className="space-y-1.5" data-testid="multi-engine-engines">
            <legend className="mb-1 text-sm text-muted-foreground">
              {t("settings_view.multi_engine.engines_label")}
            </legend>
            {engines.length === 0 && (
              <SettingsNote testId="multi-engine-no-engines">{t("settings_view.multi_engine.no_engines")}</SettingsNote>
            )}
            {engines.map((engine) => (
              <div key={engine.id} className="flex flex-wrap items-center gap-3 text-sm">
                <label className="flex min-w-40 items-center gap-2">
                  <input
                    type="checkbox"
                    checked={Boolean(picked[engine.id])}
                    disabled={busy || (!engine.authenticated && !picked[engine.id])}
                    onChange={(event) => setPicked({ ...picked, [engine.id]: event.target.checked })}
                    data-testid={`multi-engine-engine-${engine.id}`}
                  />
                  <span className="text-foreground">{engine.id}</span>
                </label>
                <span className="text-muted-foreground">
                  {t(`settings_view.fanout.kind_${engine.kind}`)}
                  {!engine.authenticated && ` · ${t("settings_view.fanout.not_connected")}`}
                </span>
                {engine.kind !== "local" && picked[engine.id] && (
                  <input
                    className={`${FIELD} w-28`}
                    inputMode="decimal"
                    placeholder={t("settings_view.fanout.cost_placeholder")}
                    aria-label={fill(t("settings_view.fanout.cost_label"), { engine: engine.id })}
                    value={costs[engine.id] ?? ""}
                    onChange={(event) => setCosts({ ...costs, [engine.id]: event.target.value })}
                    data-testid={`multi-engine-cost-${engine.id}`}
                  />
                )}
              </div>
            ))}
          </fieldset>
          <fieldset className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm" data-testid="multi-engine-synth">
            <legend className="mb-1 text-sm text-muted-foreground">
              {t("settings_view.multi_engine.synth_label")}
            </legend>
            {[{ id: FIRST_ANSWERED, label: t("settings_view.multi_engine.synth_first") }, ...chosen.map((e) => ({ id: e.id, label: e.id }))].map(
              (option) => (
                <label key={option.id || "first"} className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="multi-engine-synth"
                    checked={(chosen.some((engine) => engine.id === synth) ? synth : FIRST_ANSWERED) === option.id}
                    onChange={() => setSynth(option.id)}
                    data-testid={`multi-engine-synth-${option.id || "first"}`}
                  />
                  <span className="text-foreground">{option.label}</span>
                </label>
              ),
            )}
          </fieldset>
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <label className="flex items-center gap-2">
              <span className="text-muted-foreground">{t("settings_view.fanout.budget_label")}</span>
              <input
                className={`${FIELD} w-24`}
                inputMode="decimal"
                value={budget}
                onChange={(event) => setBudget(event.target.value)}
                aria-label={t("settings_view.fanout.budget_label")}
                data-testid="multi-engine-budget"
              />
            </label>
            <label className="flex items-center gap-2">
              <Switch
                checked={allowUnknown}
                onCheckedChange={setAllowUnknown}
                aria-label={t("settings_view.fanout.allow_unknown")}
                data-testid="multi-engine-allow-unknown"
              />
              <span className="text-muted-foreground">{t("settings_view.fanout.allow_unknown")}</span>
            </label>
          </div>
          <SettingsNote>{t("settings_view.multi_engine.budget_hint")}</SettingsNote>
          <div className="flex items-center gap-3">
            <Button size="sm" disabled={!canSave} onClick={() => void save()} data-testid="multi-engine-save">
              {t("settings_view.multi_engine.save")}
            </Button>
            {ceiling === null && (
              <span className="text-sm text-muted-foreground">{t("settings_view.multi_engine.bad_budget")}</span>
            )}
            {ceiling !== null && enabled && chosen.length === 0 && (
              <span className="text-sm text-muted-foreground">{t("settings_view.multi_engine.need_engine")}</span>
            )}
          </div>
          {message && (
            <SettingsNote tone={message.tone === "error" ? "error" : "success"} testId="multi-engine-message">
              {message.text}
            </SettingsNote>
          )}
        </SettingsRow>
      </SettingsCard>
    </SettingsSection>
  );
}

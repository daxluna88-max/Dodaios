/**
 * Settings > General > Ask several engines (ADR-0050): the first screen over `/api/consumer/fanout`.
 *
 * Explicit by construction: nothing runs until the owner types a question, ticks engines and presses Ask; the only
 * text that leaves this computer is that question; one ceiling covers all engines together; an engine without a cost
 * estimate is skipped unless the owner allows it; an engine that is not connected cannot be ticked. Hidden when the
 * backend says fan-out is not available (the Work product is local only).
 */
import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { fill, useT } from "@/i18n";
import {
  fetchFanoutStatus,
  parseCost,
  runFanout,
  type FanoutEngine,
  type FanoutResult,
  type FanoutStatus,
} from "@/lib/consumerFanout";
import {
  SettingsCard,
  SettingsNote,
  SettingsRow,
  SettingsSection,
} from "@/views/settings/SettingsLayout";

const FIELD =
  "w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground " +
  "placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

export function ConsumerFanoutGroup() {
  const t = useT();
  const [status, setStatus] = useState<FanoutStatus | null>(null);
  const [task, setTask] = useState("");
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [costs, setCosts] = useState<Record<string, string>>({});
  const [budget, setBudget] = useState("1");
  const [allowUnknown, setAllowUnknown] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<FanoutResult | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchFanoutStatus()
      .then((next) => !cancelled && setStatus(next))
      .catch(() => !cancelled && setStatus(null));
    return () => {
      cancelled = true;
    };
  }, []);

  const chosen = useMemo(
    () => (status?.providers ?? []).filter((engine) => picked[engine.id] && engine.authenticated),
    [status, picked],
  );

  if (!status || !status.available) return null;

  const ceiling = parseCost(budget);
  const canRun = !busy && task.trim() !== "" && chosen.length > 0 && ceiling !== null;

  const ask = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(
        await runFanout({
          task: task.trim(),
          budget: ceiling ?? 0,
          allow_unknown_cost: allowUnknown,
          branches: chosen.map((engine) => ({
            provider: engine.id,
            // Local engines cost no money; everything else only counts if the owner gave an estimate.
            est_cost: engine.kind === "local" ? 0 : parseCost(costs[engine.id] ?? ""),
          })),
        }),
      );
    } catch (err) {
      setError(fill(t("settings_view.fanout.error"), { detail: err instanceof Error ? err.message : String(err) }));
    } finally {
      setBusy(false);
    }
  };

  const reportFor = (provider: string) => result?.report.branches.find((b) => b.provider === provider);
  const sentFor = (provider: string) => result?.egress.find((e) => e.destination === provider);

  return (
    <SettingsSection
      title={t("settings_view.fanout.section_title")}
      description={t("settings_view.fanout.section_desc")}
    >
      <SettingsCard>
        <SettingsRow testId="fanout-form" title={t("settings_view.fanout.task_label")}>
          <textarea
            className={FIELD}
            rows={3}
            value={task}
            maxLength={20000}
            onChange={(event) => setTask(event.target.value)}
            aria-label={t("settings_view.fanout.task_label")}
            data-testid="fanout-task"
          />
          <fieldset className="space-y-1.5" data-testid="fanout-engines">
            <legend className="mb-1 text-sm text-muted-foreground">
              {t("settings_view.fanout.engines_label")}
            </legend>
            {status.providers.length === 0 && (
              <SettingsNote testId="fanout-no-engines">{t("settings_view.fanout.no_engines")}</SettingsNote>
            )}
            {status.providers.map((engine: FanoutEngine) => (
              <div key={engine.id} className="flex flex-wrap items-center gap-3 text-sm">
                <label className="flex min-w-40 items-center gap-2">
                  <input
                    type="checkbox"
                    checked={Boolean(picked[engine.id]) && engine.authenticated}
                    disabled={!engine.authenticated || busy}
                    onChange={(event) => setPicked({ ...picked, [engine.id]: event.target.checked })}
                    data-testid={`fanout-engine-${engine.id}`}
                  />
                  <span className="text-foreground">{engine.id}</span>
                </label>
                <span className="text-muted-foreground">
                  {t(`settings_view.fanout.kind_${engine.kind}`)}
                  {!engine.authenticated && ` · ${t("settings_view.fanout.not_connected")}`}
                </span>
                {engine.kind !== "local" && engine.authenticated && picked[engine.id] && (
                  <input
                    className={`${FIELD} w-28`}
                    inputMode="decimal"
                    placeholder={t("settings_view.fanout.cost_placeholder")}
                    aria-label={fill(t("settings_view.fanout.cost_label"), { engine: engine.id })}
                    value={costs[engine.id] ?? ""}
                    onChange={(event) => setCosts({ ...costs, [engine.id]: event.target.value })}
                    data-testid={`fanout-cost-${engine.id}`}
                  />
                )}
              </div>
            ))}
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
                data-testid="fanout-budget"
              />
            </label>
            <label className="flex items-center gap-2">
              <Switch
                checked={allowUnknown}
                onCheckedChange={setAllowUnknown}
                aria-label={t("settings_view.fanout.allow_unknown")}
                data-testid="fanout-allow-unknown"
              />
              <span className="text-muted-foreground">{t("settings_view.fanout.allow_unknown")}</span>
            </label>
          </div>
          <SettingsNote>{t("settings_view.fanout.budget_hint")}</SettingsNote>
          <div className="flex items-center gap-3">
            <Button size="sm" disabled={!canRun} onClick={() => void ask()} data-testid="fanout-run">
              {busy ? t("settings_view.fanout.running") : t("settings_view.fanout.run")}
            </Button>
            {!canRun && !busy && (
              <span className="text-sm text-muted-foreground">{t("settings_view.fanout.need_input")}</span>
            )}
          </div>
          {error && (
            <SettingsNote tone="error" testId="fanout-error">
              {error}
            </SettingsNote>
          )}
        </SettingsRow>
        {result && (
          <div data-testid="fanout-results" className="space-y-3 px-4 py-3.5">
            {result.branches.map((branch) => {
              const timing = reportFor(branch.provider);
              const sent = sentFor(branch.provider);
              return (
                <div key={`${branch.branch}-${branch.provider}`} className="space-y-1" data-testid={`fanout-result-${branch.provider}`}>
                  <div className="flex flex-wrap items-baseline gap-x-3 text-sm">
                    <span className="font-medium text-foreground">{branch.provider}</span>
                    <span data-testid={`fanout-status-${branch.provider}`} className="text-muted-foreground">
                      {t(`settings_view.fanout.status_${branch.status}`)}
                    </span>
                    {timing?.total_s != null && (
                      <span className="text-muted-foreground">
                        {fill(t("settings_view.fanout.total"), { s: timing.total_s.toFixed(1) })}
                      </span>
                    )}
                    {sent && (
                      <span className="text-muted-foreground">
                        {fill(t("settings_view.fanout.sent"), { chars: sent.chars })}
                      </span>
                    )}
                  </div>
                  {branch.reason && <div className="text-sm text-muted-foreground">{branch.reason}</div>}
                  {branch.text && <div className="whitespace-pre-wrap text-sm text-foreground">{branch.text}</div>}
                </div>
              );
            })}
            <div className="text-xs text-muted-foreground" data-testid="fanout-budget-used">
              {fill(t("settings_view.fanout.budget_used"), {
                used: result.report.budget_reserved,
                limit: result.report.budget_limit,
              })}
            </div>
          </div>
        )}
      </SettingsCard>
    </SettingsSection>
  );
}

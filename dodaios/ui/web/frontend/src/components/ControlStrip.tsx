/**
 * The main-screen control strip (ADR-0058): STOP, the real state, the engine of the last turn,
 * jobs, decisions waiting and a way into memory.
 *
 * What it will not do: say "Stopped" while something is still running or not known to have stopped
 * (the server decides, from the parts' own answers); say that a local engine means nothing leaves
 * the computer (inference and network/tools are two separate lines); call archiving "deletion";
 * approve in bulk or "always allow". STOP is one click, no confirmation, first in the focus order.
 * Resuming is a separate, deliberate button.
 *
 * Block 10: what is on screen is the last reading that was ORDERED and SUCCESSFUL (see
 * `lib/controlStrip.ts`). When there is none, or it is old, or the browser is offline, the state
 * is "unknown" and the last reading, if shown at all, is labelled historical and carries no
 * Approve / Deny / Cancel / Resume. STOP is always available.
 */
import { useEffect, useState } from "react";
import { Square } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import {
  useCancelJob,
  useControlStrip,
  useDecide,
  useResume,
  useStop,
  type ControlStrip as Strip,
  type Flow,
  type MissionJob,
  type PendingDecision,
  type StripSummary,
} from "@/lib/controlStrip";
import { useEventStore } from "@/store/events";

const STATE_TONE: Record<StripSummary, "default" | "accent" | "success" | "warning" | "destructive"> = {
  error: "destructive",
  stopping: "warning",
  partly_stopped: "warning",
  waiting: "warning",
  working: "accent",
  speaking: "accent",
  thinking: "accent",
  listening: "accent",
  stopped: "default",
  idle: "default",
  unknown: "warning",
};

/** Errors and decisions interrupt a screen reader; everything else waits its turn. */
const ASSERTIVE: ReadonlySet<StripSummary> = new Set(["error", "waiting"]);

function flows(list: Flow[], none: string): string {
  if (list.length === 0) return none;
  return Array.from(new Set(list.map((f) => `${f.category} → ${f.destination}`))).join(", ");
}

/** Re-render every second so a reading that stops arriving is noticed without any new data. */
function useNow(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}

export function ControlStrip() {
  const t = useT();
  const { current: data, historical, lastOkAt, failed } = useControlStrip();
  // Re-render every second so a reading that stops arriving is noticed without any new data.
  useNow(1000);
  const offline = typeof navigator !== "undefined" && navigator.onLine === false;
  const stop = useStop();
  const resume = useResume();
  const decide = useDecide();
  const cancel = useCancelJob();
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const pushToast = useEventStore((s) => s.pushToast);
  const [open, setOpen] = useState(false);

  const stoppedDoor = data ? !data.door_open : false;
  const level = (key: "active" | "idle" | "unknown") => t(`control_strip.level_${key}`);
  const count = (n: number | null) => (n === null ? level("unknown") : String(n));
  const at = (ms: number) => new Date(ms).toLocaleTimeString();
  const pendingCount = data ? data.approvals.length : 0;
  // Assertive only for errors and decisions waiting; everything else is polite.
  const assertive = !!data && (ASSERTIVE.has(data.state) || pendingCount > 0);
  const shown = data ?? historical;

  const onStop = () =>
    stop.mutate(undefined, {
      onError: (e) => pushToast("error", t("control_strip.error_stop").replace("{reason}", e.message)),
    });
  const onDecide = (decision: "approve" | "deny", d: PendingDecision) =>
    decide.mutate(
      { decision, d, generation: data?.generation ?? 0 },
      { onError: (e) => pushToast("error", t("control_strip.error_decide").replace("{reason}", e.message)) },
    );
  const onCancel = (m: MissionJob) =>
    cancel.mutate(
      { missionId: m.id },
      { onError: (e) => pushToast("error", t("control_strip.error_cancel").replace("{reason}", e.message)) },
    );

  return (
    <section
      aria-label={t("control_strip.label")}
      data-testid="control-strip"
      className="flex shrink-0 flex-col gap-2 border-b border-border bg-surface px-4 py-2"
    >
      <div className="flex flex-wrap items-center gap-3">
        {/* First in the focus order, always rendered, even when the state could not be read. */}
        <Button
          type="button"
          variant="destructive"
          size="sm"
          onClick={onStop}
          disabled={stop.isPending}
          title={t("control_strip.stop_hint")}
          aria-describedby="control-strip-stop-hint"
          data-testid="control-strip-stop"
        >
          <Square aria-hidden="true" />
          {t("control_strip.stop")}
        </Button>
        <span id="control-strip-stop-hint" className="sr-only">
          {t("control_strip.stop_hint")}
        </span>

        {stoppedDoor && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => resume.mutate()}
            disabled={resume.isPending}
            title={t("control_strip.resume_hint")}
            data-testid="control-strip-resume"
          >
            {t("control_strip.resume")}
          </Button>
        )}

        <div
          role="status"
          aria-live={assertive ? "off" : "polite"}
          className="flex flex-wrap items-center gap-2"
        >
          {data ? (
            <>
              <Badge variant={STATE_TONE[data.state]} data-testid="control-strip-state">
                {t(`control_strip.state_${data.state}`)}
              </Badge>
              {data.not_confirmed.length > 0 && (data.state === "partly_stopped" || data.state === "stopping") && (
                <span className="text-sm text-muted-foreground">
                  {t("control_strip.not_confirmed").replace(
                    "{names}",
                    data.not_confirmed.map((n) => t(`control_strip.part_${n}`)).join(", "),
                  )}
                </span>
              )}
            </>
          ) : (
            <>
              <Badge variant="warning" data-testid="control-strip-state">
                {t("control_strip.state_unknown")}
              </Badge>
              {historical ? (
                <span className="text-sm text-muted-foreground" data-testid="control-strip-stale">
                  {t("control_strip.stale_note").replace("{time}", at(lastOkAt))}
                </span>
              ) : (
                <span className="text-sm text-muted-foreground">
                  {t(failed ? "control_strip.error_load" : "control_strip.never_read")}
                </span>
              )}
              {offline && <span className="text-sm text-muted-foreground">{t("control_strip.offline_note")}</span>}
            </>
          )}
        </div>
        {/* A separate assertive region: only an error or a decision waiting interrupts. */}
        <div role="alert" aria-live="assertive" className="sr-only" data-testid="control-strip-alert">
          {assertive && data
            ? pendingCount > 0
              ? t("control_strip.approvals_alert").replace("{n}", String(pendingCount))
              : t(`control_strip.state_${data.state}`)
            : ""}
        </div>

        {data && (
          <dl className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
            <div className="flex gap-1">
              <dt>{t("control_strip.indicator_audio")}</dt>
              <dd>{level(data.indicators.audio)}</dd>
            </div>
            <div className="flex gap-1">
              <dt>{t("control_strip.indicator_jobs")}</dt>
              <dd>{count(data.indicators.jobs)}</dd>
            </div>
            <div className="flex gap-1">
              <dt>{t("control_strip.indicator_approvals")}</dt>
              <dd>{count(data.indicators.approvals)}</dd>
            </div>
            <div className="flex gap-1">
              <dt>{t("control_strip.engine_title")}</dt>
              <dd>{t(`control_strip.inference_${data.engine.inference}`)}</dd>
            </div>
          </dl>
        )}

        <div className="ml-auto flex items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            title={t("control_strip.memory_open")}
            onClick={() => setActiveSection("memory")}
            data-testid="control-strip-memory"
          >
            {t("control_strip.memory_title")}
          </Button>
          {shown && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-expanded={open}
              aria-controls="control-strip-details"
              onClick={() => setOpen(!open)}
            >
              {t(open ? "control_strip.details_hide" : "control_strip.details_show")}
            </Button>
          )}
        </div>
      </div>

      {/* Decisions come before anything else on the strip while one is pending. */}
      {data && pendingCount > 0 && (
        <Approvals
          approvals={data.approvals}
          busy={decide.isPending}
          onDecide={onDecide}
        />
      )}

      {open && shown && (
        <Details
          strip={shown}
          historicalAt={data ? null : lastOkAt}
          busy={cancel.isPending}
          onCancel={onCancel}
          onMemory={() => setActiveSection("memory")}
          at={at}
        />
      )}
    </section>
  );
}

function Approvals({
  approvals,
  busy,
  onDecide,
}: {
  approvals: PendingDecision[];
  busy: boolean;
  onDecide: (decision: "approve" | "deny", d: PendingDecision) => void;
}) {
  const t = useT();
  return (
    <div className="flex flex-col gap-1 text-sm" data-testid="control-strip-approvals">
      <h3 className="font-medium">{t("control_strip.approvals_title")}</h3>
      <p className="text-muted-foreground">{t("control_strip.approvals_one_at_a_time")}</p>
      <ul className="flex flex-col gap-2">
        {approvals.map((d) => (
          <li key={d.trace_id} className="flex flex-col gap-1 rounded-md border border-border p-2">
            <span className="font-medium">{d.tool_name}</span>
            <span className="text-muted-foreground">
              {t("control_strip.approvals_from").replace("{mission}", d.mission_id)}
              {d.risk_tier ? ` · ${d.risk_tier}` : ""}
            </span>
            {d.args_preview && <span className="break-all text-muted-foreground">{d.args_preview}</span>}
            <span className="flex gap-2">
              <Button
                type="button"
                size="sm"
                variant="default"
                disabled={busy}
                aria-label={t("control_strip.approve_label").replace("{tool}", d.tool_name)}
                onClick={() => onDecide("approve", d)}
              >
                {t("control_strip.approve")}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={busy}
                aria-label={t("control_strip.deny_label").replace("{tool}", d.tool_name)}
                onClick={() => onDecide("deny", d)}
              >
                {t("control_strip.deny")}
              </Button>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Cancel is two steps (arm, then confirm): a mission cancel cannot be undone. */
function JobRow({
  job,
  controls,
  busy,
  onCancel,
  at,
}: {
  job: MissionJob;
  controls: boolean;
  busy: boolean;
  onCancel: (m: MissionJob) => void;
  at: (ms: number) => string;
}) {
  const t = useT();
  const [armed, setArmed] = useState(false);
  const name = job.title || t("control_strip.jobs_untitled");
  return (
    <li className="flex flex-col gap-0.5" data-testid="control-strip-job">
      <span className="font-medium">{name}</span>
      <span className="text-muted-foreground">
        {job.state}
        {job.started_ms ? ` · ${t("control_strip.jobs_started").replace("{time}", at(job.started_ms))}` : ""}
      </span>
      <span className="text-muted-foreground">
        {job.tool
          ? t("control_strip.jobs_tool").replace("{tool}", job.tool)
          : t("control_strip.jobs_tool_unknown")}
        {" · "}
        {job.engine ? job.engine : t("control_strip.jobs_engine_not_observed")}
      </span>
      {controls && (
        <span>
          <Button
            type="button"
            size="sm"
            variant={armed ? "destructive" : "outline"}
            disabled={busy}
            aria-label={t("control_strip.jobs_cancel_label").replace("{name}", name)}
            onClick={() => {
              if (!armed) {
                setArmed(true);
                return;
              }
              setArmed(false);
              onCancel(job);
            }}
            onBlur={() => setArmed(false)}
          >
            {t(armed ? "control_strip.jobs_cancel_confirm" : "control_strip.jobs_cancel")}
          </Button>
        </span>
      )}
    </li>
  );
}

function Details({
  strip,
  historicalAt,
  busy,
  onCancel,
  onMemory,
  at,
}: {
  strip: Strip;
  /** Set when `strip` is NOT current: browser time of its reading; no controls are rendered. */
  historicalAt: number | null;
  busy: boolean;
  onCancel: (m: MissionJob) => void;
  onMemory: () => void;
  at: (ms: number) => string;
}) {
  const t = useT();
  const none = t("control_strip.flows_none");
  const e = strip.engine;
  const controls = historicalAt === null;
  const jobsTotal = strip.jobs.missions.length + strip.jobs.computer_use_runs + strip.jobs.background_agents;
  const learning = strip.memory?.learning_enabled;
  return (
    <div id="control-strip-details" className="flex flex-col gap-3 text-sm" data-testid="control-strip-details">
      {historicalAt !== null && (
        <p className="rounded-md border border-warning/40 p-2 text-warning" data-testid="control-strip-historical">
          <span className="font-medium">{t("control_strip.historical_label")}. </span>
          {t("control_strip.historical_banner").replace("{time}", at(historicalAt))}
        </p>
      )}
      <div className="grid gap-4 md:grid-cols-3">
      <div className="flex flex-col gap-1">
        <h3 className="font-medium">{t("control_strip.engine_title")}</h3>
        {e.state === "running" && <p className="text-muted-foreground">{t("control_strip.engine_provisional")}</p>}
        {e.state === "failed" && <p className="text-warning">{t("control_strip.engine_failed")}</p>}
        {e.state === "superseded" && <p className="text-warning">{t("control_strip.engine_superseded")}</p>}
        {e.fell_back && e.requested && (
          <p className="text-warning">{t("control_strip.engine_fell_back").replace("{requested}", e.requested)}</p>
        )}
        {e.engines.length === 0 ? (
          <p className="text-muted-foreground">{t(e.turn_id ? "control_strip.engine_no_model" : "control_strip.engine_none")}</p>
        ) : (
          <ul>
            {e.engines.map((u, i) => (
              <li key={`${u.provider}-${u.role}-${i}`}>
                {u.provider}{u.model ? ` (${u.model})` : ""} · {t(`control_strip.role_${u.role}`)} · {t(`control_strip.inference_${u.locality}`)}
              </li>
            ))}
          </ul>
        )}
        <p>
          <span className="font-medium">{t("control_strip.network_title")}: </span>
          {t(`control_strip.network_${e.network_tools}`)}
        </p>
        <p>
          <span className="font-medium">{t("control_strip.planned_title")}: </span>
          {flows(e.planned, none)}
          {e.planned_partial && <span className="text-muted-foreground"> {t("control_strip.planned_partial")}</span>}
        </p>
        <p>
          <span className="font-medium">{t("control_strip.attempted_title")}: </span>
          {flows(e.attempted, none)}
        </p>
        {e.observability === "declared" && (
          <p className="text-muted-foreground">{t("control_strip.observability_declared")}</p>
        )}
      </div>

      <div className="flex flex-col gap-1">
        <h3 className="font-medium">{t("control_strip.jobs_title")}</h3>
        {jobsTotal === 0 ? (
          <p className="text-muted-foreground">{t("control_strip.jobs_none")}</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {strip.jobs.missions.map((m) => (
              <JobRow key={m.id} job={m} controls={controls} busy={busy} onCancel={onCancel} at={at} />
            ))}
            {strip.jobs.computer_use_runs > 0 && (
              <li>
                {t("control_strip.jobs_computer_use")} · {strip.jobs.computer_use_runs}
              </li>
            )}
            {strip.jobs.background_agents > 0 && (
              <li>
                {t("control_strip.jobs_background")} · {strip.jobs.background_agents}
              </li>
            )}
          </ul>
        )}
        {(strip.jobs.computer_use_runs > 0 || strip.jobs.background_agents > 0) && (
          <p className="text-muted-foreground">{t("control_strip.jobs_no_cancel")}</p>
        )}
        {strip.stop && (
          <>
            <h3 className="mt-2 font-medium">{t("control_strip.stop_result_title")}</h3>
            <ul>
              {strip.stop.results.map((r) => (
                <li key={r.name}>
                  {t(`control_strip.part_${r.name}`)} · {t(`control_strip.outcome_${r.outcome}`)}
                </li>
              ))}
            </ul>
          </>
        )}
      </div>

      <div className="flex flex-col gap-1" data-testid="control-strip-memory-panel">
        <h3 className="font-medium">{t("control_strip.memory_title")}</h3>
        {strip.memory ? (
          <>
            <p>
              <span className="font-medium">{t("control_strip.memory_learning")}: </span>
              {t(
                learning === true
                  ? "control_strip.memory_learning_on"
                  : learning === false
                    ? "control_strip.memory_learning_off"
                    : "control_strip.memory_learning_unknown",
              )}
            </p>
            <p>
              <span className="font-medium">{t("control_strip.memory_count")}: </span>
              {strip.memory.count === null ? t("control_strip.level_unknown") : String(strip.memory.count)}
            </p>
            <ul className="list-disc pl-4 text-muted-foreground">
              <li>{t("control_strip.memory_limit_archive")}</li>
              <li>{t("control_strip.memory_limit_approval")}</li>
              <li>{t("control_strip.memory_limit_backups")}</li>
            </ul>
          </>
        ) : (
          <p className="text-muted-foreground">{t("control_strip.level_unknown")}</p>
        )}
        <span>
          <Button type="button" size="sm" variant="outline" onClick={onMemory}>
            {t("control_strip.memory_open")}
          </Button>
        </span>
      </div>
      </div>

      {strip.approvals.length === 0 && (
        <div className="flex flex-col gap-1">
          <h3 className="font-medium">{t("control_strip.approvals_title")}</h3>
          <p className="text-muted-foreground">{t("control_strip.approvals_none")}</p>
        </div>
      )}
      {strip.approvals.length > 0 && !controls && (
        <p className="text-muted-foreground">
          {t("control_strip.approvals_alert").replace("{n}", String(strip.approvals.length))}
        </p>
      )}

      <Limits strip={strip} />
    </div>
  );
}

/** What is NOT observed, straight from the server's `limits` (never filled in by the screen). */
function Limits({ strip }: { strip: Strip }) {
  const t = useT();
  const l = strip.limits;
  if (!l) return null;
  const guard =
    l.python_guard_enforced === true
      ? "limits_guard_on"
      : l.python_guard_enforced === false
        ? "limits_guard_off"
        : "limits_guard_unknown";
  const os =
    l.os_enforcement_verified === true
      ? "limits_os_verified"
      : l.os_enforcement_verified === false
        ? "limits_os_not_verified"
        : "limits_os_unknown";
  return (
    <div className="flex flex-col gap-1" data-testid="control-strip-limits">
      <h3 className="font-medium">{t("control_strip.limits_title")}</h3>
      <ul className="list-disc pl-4 text-muted-foreground">
        <li>{l.network_tools === "observed" ? t("control_strip.network_observed") : t("control_strip.limits_network")}</li>
        {!l.tools_used_observed && <li>{t("control_strip.limits_tools")}</li>}
        <li>{t(`control_strip.${guard}`)}</li>
        <li>{t(`control_strip.${os}`)}</li>
        {Object.entries(l.voice_stop).map(([part, outcome]) => (
          <li key={part}>
            {t("control_strip.limits_voice")
              .replace("{part}", t(`control_strip.part_${part}`))
              .replace("{outcome}", t(`control_strip.outcome_${outcome ?? "unknown"}`))}
          </li>
        ))}
        <li>
          {l.turn_phase
            ? t("control_strip.limits_phase").replace("{phase}", t(`control_strip.phase_${l.turn_phase}`))
            : t("control_strip.limits_phase_none")}
        </li>
      </ul>
    </div>
  );
}

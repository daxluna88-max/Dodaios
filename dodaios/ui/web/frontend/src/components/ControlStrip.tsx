/**
 * The main-screen control strip (ADR-0058): STOP, the real state, the engine of the last turn,
 * jobs, decisions waiting and a way into memory.
 *
 * What it will not do: say "Stopped" while something is still running or not known to have stopped
 * (the server decides, from the parts' own answers); say that a local engine means nothing leaves
 * the computer (inference and network/tools are two separate lines); call archiving "deletion";
 * approve in bulk or "always allow". STOP is one click, no confirmation, first in the focus order.
 * Resuming is a separate, deliberate button.
 */
import { useState } from "react";
import { Square } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import {
  useControlStrip,
  useDecide,
  useResume,
  useStop,
  type ControlStrip as Strip,
  type Flow,
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

export function ControlStrip() {
  const t = useT();
  const { data, isError } = useControlStrip();
  const stop = useStop();
  const resume = useResume();
  const decide = useDecide();
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const pushToast = useEventStore((s) => s.pushToast);
  const [open, setOpen] = useState(false);

  const stoppedDoor = data ? !data.door_open : false;
  const level = (key: "active" | "idle" | "unknown") => t(`control_strip.level_${key}`);
  const count = (n: number | null) => (n === null ? level("unknown") : String(n));

  const onStop = () =>
    stop.mutate(undefined, {
      onError: (e) => pushToast("error", t("control_strip.error_stop").replace("{reason}", e.message)),
    });

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
          aria-live={data && ASSERTIVE.has(data.state) ? "assertive" : "polite"}
          className="flex items-center gap-2"
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
            <Badge variant="warning" data-testid="control-strip-state">
              {t(isError ? "control_strip.error_load" : "control_strip.state_unknown")}
            </Badge>
          )}
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
          <Button type="button" variant="ghost" size="sm" onClick={() => setActiveSection("memory")}>
            {t("control_strip.memory_title")}
          </Button>
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
        </div>
      </div>

      {open && data && <Details strip={data} onDecide={(decision, d) => decide.mutate({ decision, d })} />}
    </section>
  );
}

function Details({
  strip,
  onDecide,
}: {
  strip: Strip;
  onDecide: (decision: "approve" | "deny", d: Strip["approvals"][number]) => void;
}) {
  const t = useT();
  const none = t("control_strip.flows_none");
  const e = strip.engine;
  const jobsTotal = strip.jobs.missions.length + strip.jobs.computer_use_runs + strip.jobs.background_agents;
  return (
    <div id="control-strip-details" className="grid gap-4 text-sm md:grid-cols-3" data-testid="control-strip-details">
      <div className="flex flex-col gap-1">
        <h3 className="font-medium">{t("control_strip.engine_title")}</h3>
        {e.engines.length === 0 ? (
          <p className="text-muted-foreground">{t("control_strip.engine_none")}</p>
        ) : (
          <ul>
            {e.engines.map((u, i) => (
              <li key={`${u.provider}-${u.role}-${i}`}>
                {u.provider} · {t(`control_strip.role_${u.role}`)} · {t(`control_strip.inference_${u.locality}`)}
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
          <ul>
            {strip.jobs.missions.map((m) => (
              <li key={m.id}>
                {t("control_strip.jobs_missions")} · {m.state}
              </li>
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

      <div className="flex flex-col gap-1">
        <h3 className="font-medium">{t("control_strip.approvals_title")}</h3>
        {strip.approvals.length === 0 ? (
          <p className="text-muted-foreground">{t("control_strip.approvals_none")}</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {strip.approvals.map((d) => (
              <li key={d.trace_id} className="flex flex-col gap-1 rounded-md border border-border p-2">
                <span className="font-medium">{d.tool_name}</span>
                {d.args_preview && <span className="break-all text-muted-foreground">{d.args_preview}</span>}
                <span className="flex gap-2">
                  <Button type="button" size="sm" variant="default" onClick={() => onDecide("approve", d)}>
                    {t("control_strip.approve")}
                  </Button>
                  <Button type="button" size="sm" variant="outline" onClick={() => onDecide("deny", d)}>
                    {t("control_strip.deny")}
                  </Button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

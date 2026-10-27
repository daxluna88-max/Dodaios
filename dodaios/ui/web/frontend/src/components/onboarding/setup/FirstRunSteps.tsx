/**
 * The steps the guided first run adds around the existing name / connect / voice steps (issue 18):
 * personality, privacy and actions, and the honest summary.
 *
 * Everything here is optional to the owner, nothing here asks for a permission, starts a download, connects a
 * service or spends money. The personality previews are static sentences (no model, no API). The summary reads the
 * configuration back from the server after it was written, so it says what IS set, and it never says an engine was
 * "verified": a model is not checked without a request.
 */
import { ArrowRight, Loader2 } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { fill, useLocaleChunk, useT } from "@/i18n";
import type { FirstRunChoices, FirstRunEngine, FirstRunState, Interaction, useFirstRun } from "@/lib/firstRun";
import { BackAction, StepFooter, StepHead } from "./wizardParts";

export type FirstRun = ReturnType<typeof useFirstRun>;

const FIELD =
  "mt-2 h-11 w-full rounded-lg border border-border-strong bg-input px-4 text-base text-foreground " +
  "placeholder:text-foreground-faint focus-visible:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

/* --------------------------------------------------------------- welcome */

/** The owner's name (and, for Work, the space's name): optional, kept on this computer, never sent to an engine. */
export function OwnerFields({ run }: { run: FirstRun }) {
  const t = useT();
  const state = run.state;
  if (!state) return null;
  const work = state.product === "work";
  return (
    <div className="mt-6 space-y-4" data-testid="setup-owner-fields">
      <label className="block text-sm text-muted-foreground">
        {work ? t("first_run.name.owner_label_work") : t("first_run.name.owner_label")}
        <input
          value={state.choices.owner_name ?? ""}
          onChange={(event) => void run.update({ owner_name: event.target.value || null })}
          placeholder={t("first_run.name.owner_placeholder")}
          maxLength={60}
          autoComplete="off"
          data-testid="setup-owner-name"
          className={FIELD}
        />
      </label>
      {work && (
        <label className="block text-sm text-muted-foreground">
          {t("first_run.name.space_label")}
          <input
            value={state.choices.space_name ?? ""}
            onChange={(event) => void run.update({ space_name: event.target.value || null })}
            placeholder={t("first_run.name.space_placeholder")}
            maxLength={60}
            autoComplete="off"
            data-testid="setup-space-name"
            className={FIELD}
          />
        </label>
      )}
      <p className="text-xs leading-relaxed text-muted-foreground">{t("first_run.name.local_note")}</p>
    </div>
  );
}

/* ----------------------------------------------------------- interaction */

const INTERACTIONS: readonly Interaction[] = ["text", "voice", "both"];

/** Text, voice or both. Choosing voice asks for nothing: the microphone is requested when it is first used. */
export function InteractionChoice({ run }: { run: FirstRun }) {
  const t = useT();
  const state = run.state;
  if (!state) return null;
  return (
    <fieldset className="mt-5" data-testid="setup-interaction">
      <legend className="text-sm text-muted-foreground">{t("first_run.voice.mode_label")}</legend>
      <div className="mt-2 space-y-2">
        {INTERACTIONS.map((mode) => (
          <label key={mode} className="flex items-start gap-3 rounded-lg border border-border bg-popover px-3.5 py-3">
            <input
              type="radio"
              name="setup-interaction"
              className="mt-1"
              checked={state.choices.interaction === mode}
              onChange={() => void run.update({ interaction: mode })}
              data-testid={`setup-interaction-${mode}`}
            />
            <span>
              <span className="block text-sm font-medium text-foreground">{t(`first_run.voice.mode_${mode}`)}</span>
              <span className="block text-xs leading-relaxed text-muted-foreground">
                {t(`first_run.voice.mode_${mode}_hint`)}
              </span>
            </span>
          </label>
        ))}
      </div>
      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{t("first_run.voice.mic_note")}</p>
    </fieldset>
  );
}

/* ----------------------------------------------------------- personality */

export function PersonalityStep({
  run,
  onBack,
  onContinue,
  onSkip,
}: {
  run: FirstRun;
  onBack: () => void;
  onContinue: () => void;
  onSkip: () => void;
}) {
  const t = useT();
  const state = run.state;
  if (!state) return <Waiting />;
  const chosen = state.choices.personality_preset ?? state.default_preset;
  const choose = (patch: Partial<FirstRunChoices>) => void run.update({ personality_skip: false, ...patch });
  return (
    <>
      <StepHead title={t("first_run.personality.title")} lede={t("first_run.personality.lede")} />
      <fieldset className="mt-5" data-testid="setup-personality-presets">
        <legend className="sr-only">{t("first_run.personality.presets_label")}</legend>
        <div className="space-y-2">
          {state.presets.map((preset) => (
            <label key={preset} className="flex items-start gap-3 rounded-lg border border-border bg-popover px-3.5 py-3">
              <input
                type="radio"
                name="setup-personality-preset"
                className="mt-1"
                checked={!state.choices.personality_skip && chosen === preset}
                onChange={() => choose({ personality_preset: preset })}
                data-testid={`setup-personality-${preset}`}
              />
              <span>
                <span className="block text-sm font-medium text-foreground">{t(`first_run.personality.${preset}`)}</span>
                <span className="block text-xs italic leading-relaxed text-muted-foreground">
                  {t(`first_run.personality.${preset}_sample`)}
                </span>
              </span>
            </label>
          ))}
        </div>
        <p className="mt-2 text-xs text-muted-foreground">{t("first_run.personality.sample_note")}</p>
      </fieldset>
      <fieldset className="mt-5" data-testid="setup-personality-tone">
        <legend className="text-sm text-muted-foreground">{t("first_run.personality.tone_label")}</legend>
        <div className="mt-2 space-y-2">
          {(["keep", "adapt"] as const).map((tone) => (
            <label key={tone} className="flex items-start gap-3">
              <input
                type="radio"
                name="setup-personality-tone"
                className="mt-1"
                checked={state.choices.personality_tone === tone}
                onChange={() => choose({ personality_tone: tone })}
                data-testid={`setup-personality-tone-${tone}`}
              />
              <span>
                <span className="block text-sm text-foreground">{t(`first_run.personality.tone_${tone}`)}</span>
                <span className="block text-xs text-muted-foreground">{t(`first_run.personality.tone_${tone}_hint`)}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      <p className="mt-4 text-xs leading-relaxed text-muted-foreground">{t("first_run.personality.simulated")}</p>
      <StepFooter left={<BackAction onClick={onBack} />}>
        <Button
          variant="outline"
          onClick={() => {
            void run.update({ personality_skip: true });
            onSkip();
          }}
          data-testid="setup-personality-skip"
        >
          {t("first_run.personality.skip")}
        </Button>
        <Button onClick={onContinue} data-testid="onboarding-primary">
          {t("first_run.continue")}
          <ArrowRight aria-hidden />
        </Button>
      </StepFooter>
    </>
  );
}

/* ---------------------------------------------------------------- engine */

/** What to tell the owner under the connect step: three kinds of engine, and that a plan is not API credit. */
export function EngineNotes({ product }: { product: "consumer" | "work" }) {
  const t = useT();
  if (product === "work") return null;
  return (
    <div className="mt-4 space-y-1.5 text-xs leading-relaxed text-muted-foreground" data-testid="setup-engine-notes">
      <p>{t("first_run.connect.one_first")}</p>
      <p>{t("first_run.connect.kinds_note")}</p>
    </div>
  );
}

/** Work uses local engines only: nothing is connected here, and nothing falls back to the cloud. */
export function WorkEnginePanel({
  state,
  onBack,
  onContinue,
}: {
  state: FirstRunState | null;
  onBack: () => void;
  onContinue: () => void;
}) {
  const t = useT();
  const engine = state?.summary?.engine;
  return (
    <>
      <StepHead title={t("first_run.connect.work_title")} lede={t("first_run.connect.work_lede")} />
      <div className="mt-5 rounded-lg border border-border bg-popover px-4 py-3.5 text-sm" data-testid="setup-work-engine">
        <p className="text-foreground">{t("first_run.connect.work_local_only")}</p>
        {engine && (
          <p className="mt-2 text-muted-foreground" data-testid="setup-work-engine-state">
            {engineLine(t, engine)}
          </p>
        )}
        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{t("first_run.connect.work_need")}</p>
      </div>
      <StepFooter left={<BackAction onClick={onBack} />}>
        <Button onClick={onContinue} data-testid="onboarding-primary">
          {t("first_run.continue")}
          <ArrowRight aria-hidden />
        </Button>
      </StepFooter>
    </>
  );
}

/* --------------------------------------------------------------- privacy */

export function PrivacyStep({
  run,
  onBack,
  onApplied,
}: {
  run: FirstRun;
  onBack: () => void;
  onApplied: () => void;
}) {
  const t = useT();
  const [problem, setProblem] = useState<string | null>(null);
  const state = run.state;
  if (!state) return <Waiting />;
  const work = state.product === "work";

  async function next() {
    setProblem(null);
    try {
      await run.apply();
      onApplied();
    } catch (e) {
      // Nothing was marked done: stay here, say so, and let the owner try again.
      setProblem(fill(t("first_run.summary.error"), { detail: (e as Error).message }));
    }
  }

  return (
    <>
      <StepHead title={t("first_run.privacy.title")} lede={t("first_run.privacy.lede")} />
      <div className="mt-5 space-y-3 text-sm leading-relaxed text-foreground" data-testid="setup-privacy-text">
        <p>{work ? t("first_run.privacy.work_local") : t("first_run.privacy.consumer_local")}</p>
        <p className="text-muted-foreground">{work ? t("first_run.privacy.work_offline") : t("first_run.privacy.consumer_remote")}</p>
      </div>
      <div className="mt-5 flex items-start justify-between gap-4 rounded-lg border border-border bg-popover px-4 py-3.5">
        <div>
          <p className="text-sm font-medium text-foreground">{t("first_run.privacy.memory_label")}</p>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground" data-testid="setup-memory-hint">
            {state.choices.memory === "on" ? t("first_run.privacy.memory_on") : t("first_run.privacy.memory_off")}
          </p>
        </div>
        <Switch
          checked={state.choices.memory === "on"}
          onCheckedChange={(on) => void run.update({ memory: on ? "on" : "off" })}
          aria-label={t("first_run.privacy.memory_label")}
          data-testid="setup-memory-switch"
        />
      </div>
      <div className="mt-3 rounded-lg border border-border bg-popover px-4 py-3.5">
        <p className="text-sm font-medium text-foreground">{t("first_run.privacy.actions_title")}</p>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{t("first_run.privacy.actions_body")}</p>
      </div>
      {problem && (
        <p role="alert" className="mt-3 text-xs text-destructive" data-testid="setup-apply-error">
          {problem}
        </p>
      )}
      <StepFooter left={<BackAction onClick={onBack} />}>
        <Button onClick={() => void next()} disabled={run.busy} data-testid="onboarding-primary">
          {run.busy && <Loader2 aria-hidden className="animate-spin" />}
          {t("first_run.continue")}
          <ArrowRight aria-hidden />
        </Button>
      </StepFooter>
    </>
  );
}

/* --------------------------------------------------------------- summary */

function engineLine(t: (key: string) => string, engine: FirstRunEngine): string {
  if (!engine.provider) return t("first_run.summary.engine_state_no_engine");
  const where = engine.local === true ? t("first_run.summary.where_local") : t("first_run.summary.where_remote");
  const kind = engine.kind ? t(`first_run.summary.kind_${engine.kind}`) : "";
  const hardened = engine.reason === "local_not_found" || engine.reason === "local_unconfirmed";
  const state = hardened
    ? t(`first_run_hardening.reason_${engine.reason}`)
    : t(`first_run.summary.engine_state_${engine.reason || "no_engine"}`);
  return [engine.provider, kind, where, state].filter(Boolean).join(" · ");
}

/** Block 11: the engine levels, the permissions (each listed as on/off) and an unfinished earlier apply. */
function HardeningRows({ summary }: { summary: NonNullable<FirstRunState["summary"]> }) {
  useLocaleChunk("first_run_hardening");
  const t = useT();
  const h = (key: string) => t(`first_run_hardening.${key}`);
  const value = (v: string) => h(`value_${v}`);
  const levels = summary.engine.levels;
  const permissions = summary.permissions ?? [];
  const on = permissions.filter((p) => p.state === "on").map((p) => h(`perm_${p.id}`));
  const off = permissions.filter((p) => p.state === "off").map((p) => h(`perm_${p.id}`));
  const journal = summary.journal;
  const voice = summary.components?.voice;
  return (
    <div className="mt-4 space-y-4 text-sm" data-testid="setup-hardening">
      {levels && (
        <section data-testid="setup-engine-levels">
          <h3 className="font-medium text-foreground">{h("levels_title")}</h3>
          <dl className="mt-2 divide-y divide-border rounded-lg border border-border bg-popover">
            {(["configured", "detected", "reachable", "verified"] as const).map((level) => (
              <div key={level} className="flex justify-between gap-4 px-4 py-2">
                <dt className="text-muted-foreground">{h(`level_${level}`)}</dt>
                <dd className="text-foreground">{value(levels[level])}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-2 text-xs text-muted-foreground">{h("levels_note")}</p>
        </section>
      )}
      {voice && (
        <p className="text-muted-foreground" data-testid="setup-voice-component">
          {h("voice_title")}:{" "}
          {voice.needed !== "yes"
            ? h("voice_not_needed")
            : voice.speech_package === "present"
              ? h("voice_package_present")
              : h("voice_package_missing")}
        </p>
      )}
      {permissions.length > 0 && (
        <section data-testid="setup-permissions">
          <h3 className="font-medium text-foreground">{h("permissions_title")}</h3>
          <p className="mt-1 text-xs text-muted-foreground">{h("permissions_lede")}</p>
          <dl className="mt-2 divide-y divide-border rounded-lg border border-border bg-popover">
            {permissions.map((p) => (
              <div key={p.id} className="flex justify-between gap-4 px-4 py-2">
                <dt className="text-muted-foreground">
                  {h(`perm_${p.id}`)} <span className="text-xs">({h(`note_${p.note}`)})</span>
                </dt>
                <dd className="text-foreground">{h(`state_${p.state}`)}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-2 text-xs text-foreground" data-testid="setup-permissions-enabled">
            {h("enabled_title")}: {on.length ? on.join(", ") : h("none")}
          </p>
          <p className="text-xs text-muted-foreground" data-testid="setup-permissions-off">
            {h("left_off_title")}: {off.length ? off.join(", ") : h("none")}
          </p>
        </section>
      )}
      {journal && journal.resume && (
        <p role="status" className="text-xs text-muted-foreground" data-testid="setup-journal">
          {fill(h("journal_unfinished"), { done: journal.done.join(", ") || h("journal_nothing") })}
        </p>
      )}
    </div>
  );
}

export function SummaryStep({
  run,
  preview,
  onEdit,
  onFinish,
}: {
  run: FirstRun;
  preview: boolean;
  onEdit: () => void;
  onFinish: () => void;
}) {
  const t = useT();
  const state = run.state;
  const summary = state?.summary ?? null;
  if (!state) return <Waiting />;
  const rows: Array<[string, string]> = summary
    ? [
        [t("first_run.summary.row_interaction"), t(`first_run.voice.mode_${summary.interaction}`)],
        [
          t("first_run.summary.row_personality"),
          `${t(`first_run.personality.${summary.personality.preset}`)} · ${t(`first_run.summary.mode_${summary.personality.mode}`)}`,
        ],
        [t("first_run.summary.row_engine"), engineLine(t, summary.engine)],
        [
          t("first_run.summary.row_memory"),
          summary.memory.learning_enabled ? t("first_run.summary.memory_on") : t("first_run.summary.memory_off"),
        ],
        [
          t("first_run.summary.row_actions"),
          summary.actions.approval_mode === "ask"
            ? t("first_run.summary.actions_ask")
            : t("first_run.summary.actions_other"),
        ],
      ]
    : [];
  const ready = summary?.start === "start";
  return (
    <>
      <StepHead title={t("first_run.summary.title")} lede={t("first_run.summary.lede")} />
      {summary ? (
        <dl className="mt-5 divide-y divide-border overflow-hidden rounded-lg border border-border bg-popover" data-testid="setup-summary">
          {rows.map(([label, value]) => (
            <div key={label} className="grid grid-cols-1 gap-1 px-4 py-3 text-sm sm:grid-cols-[9rem_minmax(0,1fr)] sm:gap-4">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="text-foreground">{value}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="mt-5 text-sm text-muted-foreground" data-testid="setup-summary-missing">
          {t("first_run.summary.unavailable")}
        </p>
      )}
      {summary && <HardeningRows summary={summary} />}
      <p className="mt-4 text-sm leading-relaxed text-foreground" data-testid="setup-summary-ready">
        {ready
          ? state.product === "work"
            ? t("first_run.summary.ready_work")
            : t("first_run.summary.ready_consumer")
          : t("first_run.summary.not_ready")}
      </p>
      {preview && <p className="mt-2 text-xs text-muted-foreground">{t("first_run.summary.preview_note")}</p>}
      <StepFooter left={<BackAction onClick={onEdit} />}>
        <Button variant="outline" onClick={onEdit} data-testid="setup-edit">
          {t("first_run.summary.edit")}
        </Button>
        <Button onClick={onFinish} data-testid="onboarding-start">
          {ready ? t("first_run.summary.start") : t("first_run.summary.explore")}
          <ArrowRight aria-hidden />
        </Button>
      </StepFooter>
    </>
  );
}

function Waiting() {
  const t = useT();
  return (
    <p className="text-sm text-muted-foreground" role="status" data-testid="setup-waiting">
      {t("first_run.summary.waiting")}
    </p>
  );
}

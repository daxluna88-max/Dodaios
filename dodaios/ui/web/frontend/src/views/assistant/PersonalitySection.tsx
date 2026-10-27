/**
 * Personality — how the assistant speaks, chosen by the owner, and its simulated mood.
 *
 * Stable part: mode (manual / adaptive / mixed), a preset and four bounded dials, saved only when the
 * owner presses Save and reset with one button. Temporary part: the mood, which is SIMULATED, never a
 * feeling and never a reading of the owner. It shows what it is and why, and the owner can set it,
 * hold it, return it to neutral or turn adaptation off; those requests last this session only.
 * Nothing here grants a tool, an approval or a budget.
 */
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import {
  previewPersonality,
  useMoodAction,
  useResetPersonality,
  usePersonality,
  useSavePersonality,
  type Dimension,
  type PersonalityMode,
  type PersonalityProfile,
} from "@/views/assistant/personalityApi";
import { PersonalityLimits } from "@/views/assistant/PersonalityLimits";
import { Section, SpecRow, TextAction } from "@/views/assistant/Section";

const MODES: readonly PersonalityMode[] = ["manual", "adaptive", "mixed"];
const DIMENSIONS: readonly Dimension[] = ["formality", "warmth", "humor", "concision"];

type Draft = Pick<PersonalityProfile, "mode" | "preset" | "tone" | Dimension>;

function draftOf(profile: PersonalityProfile): Draft {
  const { mode, preset, tone, formality, warmth, humor, concision } = profile;
  return { mode, preset, tone, formality, warmth, humor, concision };
}

export function PersonalitySection() {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const { data } = usePersonality();
  const save = useSavePersonality();
  const reset = useResetPersonality();
  const moodAction = useMoodAction();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [preview, setPreview] = useState("");

  useEffect(() => {
    if (data && draft === null) setDraft(draftOf(data.profile));
  }, [data, draft]);

  if (!data || !draft) return null;

  const choice = (key: keyof Draft, value: string | number | null) => setDraft({ ...draft, [key]: value });
  const preset = draft.preset ?? data.default_preset;
  const shown = (dim: Dimension) => draft[dim] ?? data.presets[preset]?.[dim] ?? 0;
  const dirty = JSON.stringify(draft) !== JSON.stringify(draftOf(data.profile));
  const busy = save.isPending || reset.isPending;
  const mood = data.mood;
  const reasonText = (code: string) => {
    const key = `assistant_view.personality_reason_${code}`;
    const text = t(key);
    return text === key ? code : text;
  };
  const doMood = (body: Parameters<typeof moodAction.mutate>[0]) =>
    moodAction.mutate(body, { onError: (e) => pushToast("error", e.message) });

  const onSave = () =>
    // The whole profile: fields this page does not edit (range, allowed moods, pin) are kept.
    save.mutate({ ...data.profile, ...draft }, {
      onSuccess: (state) => setDraft(draftOf(state.profile)),
      onError: (e) => pushToast("error", e.message),
    });
  const onReset = () =>
    reset.mutate(undefined, {
      onSuccess: (state) => setDraft(draftOf(state.profile)),
      onError: (e) => pushToast("error", e.message),
    });
  const onPreview = () =>
    previewPersonality(draft)
      .then(setPreview)
      .catch((e: Error) => pushToast("error", e.message));

  return (
    <Section
      testId="assistant-personality"
      title={t("assistant_view.personality_title")}
      action={
        <TextAction onClick={onReset} disabled={busy} data-testid="personality-reset">
          {t("assistant_view.personality_reset")}
        </TextAction>
      }
    >
      <p className="max-w-reading text-base leading-7 text-muted-foreground">{t("assistant_view.personality_hint")}</p>
      {data.status !== "ok" && data.status !== "migrated" && (
        <p role="status" className="mt-2 text-sm text-warning" data-testid="personality-status">
          {t(`assistant_view.personality_status_${data.status}`)}
        </p>
      )}

      <div className="mt-2 divide-y divide-border">
        <SpecRow label={t("assistant_view.personality_mode")}>
          <div role="radiogroup" aria-label={t("assistant_view.personality_mode")} className="flex flex-col gap-1.5">
            {MODES.map((m) => (
              <label key={m} className="flex items-baseline gap-2.5">
                <input
                  type="radio"
                  name="personality-mode"
                  checked={draft.mode === m}
                  onChange={() => choice("mode", m)}
                  data-testid={`personality-mode-${m}`}
                />
                <span className={draft.mode === m ? "font-medium text-foreground-strong" : "text-muted-foreground"}>
                  {t(`assistant_view.personality_mode_${m}`)}
                </span>
                <span className="text-sm text-muted-foreground">{t(`assistant_view.personality_mode_${m}_hint`)}</span>
              </label>
            ))}
          </div>
        </SpecRow>

        <SpecRow label={t("assistant_view.personality_preset")}>
          <div role="radiogroup" aria-label={t("assistant_view.personality_preset")} className="flex flex-wrap gap-x-5 gap-y-1.5">
            {Object.keys(data.presets).map((name) => (
              <label key={name} className="flex items-center gap-2">
                <input
                  type="radio"
                  name="personality-preset"
                  checked={preset === name}
                  onChange={() => setDraft({ ...draft, preset: name, tone: null, formality: null, warmth: null, humor: null, concision: null })}
                  data-testid={`personality-preset-${name}`}
                />
                <span className={preset === name ? "font-medium text-foreground-strong" : "text-muted-foreground"}>
                  {t(`assistant_view.personality_preset_${name}`)}
                </span>
              </label>
            ))}
          </div>
        </SpecRow>

        <SpecRow label={t("assistant_view.personality_tone")}>
          <div role="radiogroup" aria-label={t("assistant_view.personality_tone")} className="flex flex-wrap gap-x-5 gap-y-1.5">
            {data.tones.map((tone) => (
              <label key={tone} className="flex items-center gap-2">
                <input
                  type="radio"
                  name="personality-tone"
                  checked={(draft.tone ?? data.presets[preset]?.tone) === tone}
                  onChange={() => choice("tone", tone)}
                  data-testid={`personality-tone-${tone}`}
                />
                <span className="text-foreground">{t(`assistant_view.personality_tone_${tone}`)}</span>
              </label>
            ))}
          </div>
        </SpecRow>

        {DIMENSIONS.map((dim) => (
          <SpecRow key={dim} label={t(`assistant_view.personality_${dim}`)}>
            <div className="flex items-center gap-3">
              <input
                type="range"
                min={0}
                max={4}
                step={1}
                value={shown(dim)}
                onChange={(event) => choice(dim, Number(event.target.value))}
                aria-label={t(`assistant_view.personality_${dim}`)}
                data-testid={`personality-${dim}`}
                className="w-48"
              />
              <span className="text-sm text-muted-foreground">{t(`assistant_view.personality_${dim}_${shown(dim)}`)}</span>
            </div>
          </SpecRow>
        ))}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Button size="sm" disabled={!dirty || busy} onClick={onSave} data-testid="personality-save">
          {t("assistant_view.personality_save")}
        </Button>
        <TextAction onClick={() => void onPreview()} data-testid="personality-preview">
          {t("assistant_view.personality_preview")}
        </TextAction>
        {dirty && <span className="text-sm text-muted-foreground">{t("assistant_view.personality_unsaved")}</span>}
      </div>
      {preview && (
        <p className="mt-2 max-w-reading whitespace-pre-wrap text-sm text-muted-foreground" data-testid="personality-preview-text">
          {preview}
        </p>
      )}

      <div className="mt-6 border-t border-border pt-4" data-testid="personality-mood">
        <h3 className="text-base font-semibold text-foreground-strong">{t("assistant_view.personality_mood_title")}</h3>
        <p className="mt-1 max-w-reading text-sm leading-6 text-muted-foreground">{t("assistant_view.personality_simulated")}</p>
        <p className="mt-2 text-base text-foreground" data-testid="personality-mood-now">
          {t(`assistant_view.personality_mood_${mood.mood}`)}
          {mood.intensity > 0 && ` · ${mood.intensity}/3`}
          {" — "}
          <span className="text-muted-foreground">{reasonText(mood.reason)}</span>
        </p>
        {!data.mood_available && (
          <p className="mt-1 text-sm text-muted-foreground">{t("assistant_view.personality_mood_starting")}</p>
        )}
        <div className="mt-2 flex flex-wrap gap-2">
          {data.moods
            .filter((m) => m !== "neutral")
            .map((m) => (
              <Button
                key={m}
                size="sm"
                variant="outline"
                disabled={!data.mood_available || moodAction.isPending}
                onClick={() => doMood({ action: "set", mood: m })}
                data-testid={`personality-set-${m}`}
                className={cn(mood.mood === m && "border-foreground-strong")}
              >
                {t(`assistant_view.personality_mood_${m}`)}
              </Button>
            ))}
          <Button size="sm" variant="outline" disabled={!data.mood_available || moodAction.isPending} onClick={() => doMood({ action: "hold" })} data-testid="personality-hold">
            {t("assistant_view.personality_hold")}
          </Button>
          <Button size="sm" variant="outline" disabled={!data.mood_available || moodAction.isPending} onClick={() => doMood({ action: "neutral" })} data-testid="personality-neutral">
            {t("assistant_view.personality_neutral")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={!data.mood_available || moodAction.isPending}
            onClick={() => doMood({ action: "adaptation", enabled: !mood.adaptation_on })}
            data-testid="personality-adaptation"
          >
            {mood.adaptation_on ? t("assistant_view.personality_adaptation_off") : t("assistant_view.personality_adaptation_on")}
          </Button>
        </div>
        <p className="mt-2 text-sm text-muted-foreground">{t("assistant_view.personality_session_only")}</p>
      </div>

      <PersonalityLimits data={data} />
    </Section>
  );
}

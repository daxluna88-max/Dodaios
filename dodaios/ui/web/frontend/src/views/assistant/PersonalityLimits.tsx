/**
 * Limits of adaptation (ADR-0056, block 12).
 *
 * Says in plain words the difference between the RANGE OF AUTOMATIC ADAPTATION (what the assistant
 * may do on its own) and the owner's EXPLICIT REQUEST (which may go beyond that range but never
 * beyond the allowed moods or the maximum intensity), and what the style does and does not reach
 * (wording yes; voice modulation, avatar and lip sync no). The numbers come from the server; this
 * component decides nothing.
 */
import { fill, useLocaleChunk, useT } from "@/i18n";
import type { PersonalityState } from "@/views/assistant/personalityApi";

const NS = "personality_limits";

export function PersonalityLimits({ data }: { data: PersonalityState }) {
  const t = useT();
  const ready = useLocaleChunk("personality_limits");
  const limits = data.limits;
  if (!ready || !limits) return null;
  const k = (key: string) => t(`${NS}.${key}`);
  const max = limits.owner_max_intensity;
  const auto = limits.adaptation_max_intensity;
  const moodName = (m: string) => t(`assistant_view.personality_mood_${m}`);
  const delivery = Object.entries(data.realtime_delivery ?? {});

  return (
    <div className="mt-6 border-t border-border pt-4" data-testid="personality-limits">
      <h3 className="text-base font-semibold text-foreground-strong">{k("title")}</h3>

      <div className="mt-2" data-testid="personality-limits-auto">
        <h4 className="text-sm font-medium text-foreground">{k("auto_title")}</h4>
        <p className="max-w-reading text-sm leading-6 text-muted-foreground">{k("auto_body")}</p>
        <p className="mt-1 text-sm text-foreground" data-testid="personality-limits-auto-value">
          {auto > 0 ? fill(k("auto_value"), { n: auto, max }) : k("auto_off")}
        </p>
      </div>

      <div className="mt-3" data-testid="personality-limits-owner">
        <h4 className="text-sm font-medium text-foreground">{k("owner_title")}</h4>
        <p className="max-w-reading text-sm leading-6 text-muted-foreground">{fill(k("owner_body"), { max })}</p>
        <p className="mt-1 text-sm text-foreground" data-testid="personality-limits-owner-value">
          {fill(k("owner_value"), { max })}
        </p>
      </div>

      <div className="mt-3" data-testid="personality-limits-moods">
        <h4 className="text-sm font-medium text-foreground">{k("moods_title")}</h4>
        <p className="max-w-reading text-sm leading-6 text-muted-foreground">{k("moods_body")}</p>
        <p className="mt-1 text-sm text-foreground">{limits.moods_allowed.map(moodName).join(", ")}</p>
        {limits.locked_mood && (
          <p className="text-sm text-muted-foreground">{fill(k("pinned"), { mood: moodName(limits.locked_mood) })}</p>
        )}
      </div>

      <div className="mt-3" data-testid="personality-limits-never">
        <h4 className="text-sm font-medium text-foreground">{k("never_title")}</h4>
        <p className="max-w-reading text-sm leading-6 text-muted-foreground">{k("never_body")}</p>
      </div>

      <div className="mt-3" data-testid="personality-limits-channels">
        <h4 className="text-sm font-medium text-foreground">{k("channels_title")}</h4>
        <ul className="max-w-reading list-disc pl-5 text-sm leading-6 text-muted-foreground">
          <li>{k("ch_text")}</li>
          <li>{k("ch_realtime")}</li>
          <li>{k("ch_voice")}</li>
          <li>{k("ch_avatar")}</li>
        </ul>
        {delivery.length > 0 && (
          <>
            <h4 className="mt-2 text-sm font-medium text-foreground">{k("delivery_title")}</h4>
            <ul className="text-sm text-muted-foreground" data-testid="personality-limits-delivery">
              {delivery.map(([provider, when]) => (
                <li key={provider}>
                  {provider}: {k(`delivery_${when}`)}
                </li>
              ))}
            </ul>
          </>
        )}
        <p className="mt-2 text-sm text-muted-foreground">{k("stop_note")}</p>
      </div>
    </div>
  );
}

import { useEffect, useState } from "react";
import { useT } from "@/i18n";
import { Switch } from "@/components/ui/switch";
import { SettingsCard, SettingsRow, SettingsSection } from "@/views/settings/SettingsLayout";

const ENDPOINT = "/api/settings/personal-context";

/**
 * "Privacy" section of the General settings page. One switch: whether the
 * personal context (profile, memory, people, contacts) rides along in every
 * request to a model provider. Off keeps all of it on this machine; the effect
 * starts with the next turn.
 */
export function PrivacyGroup() {
  const t = useT();
  const [allowed, setAllowed] = useState<boolean | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    let live = true;
    fetch(ENDPOINT)
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(String(res.status)))))
      .then((body: { allowed?: boolean }) => {
        if (live) setAllowed(body.allowed !== false);
      })
      .catch(() => {
        if (live) setError(true);
      });
    return () => {
      live = false;
    };
  }, []);

  async function change(next: boolean) {
    const previous = allowed;
    setAllowed(next);
    setSaving(true);
    setError(false);
    try {
      const res = await fetch(ENDPOINT, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ allowed: next }),
      });
      if (!res.ok) throw new Error(String(res.status));
    } catch {
      setAllowed(previous);
      setError(true);
    } finally {
      setSaving(false);
    }
  }

  return (
    <SettingsSection title={t("settings_view.privacy_group_title")}>
      <SettingsCard>
        <SettingsRow
          title={t("settings_view.personal_context_title")}
          description={t("settings_view.personal_context_description")}
          control={
            <Switch
              checked={allowed ?? true}
              disabled={allowed === null || saving}
              aria-label={t("settings_view.personal_context_title")}
              onCheckedChange={(next) => void change(next)}
            />
          }
        >
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {t("settings_view.personal_context_error")}
            </p>
          )}
        </SettingsRow>
      </SettingsCard>
    </SettingsSection>
  );
}

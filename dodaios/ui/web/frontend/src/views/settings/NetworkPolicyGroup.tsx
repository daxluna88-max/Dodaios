/**
 * Settings > General > Network: the opt-in strict offline profile (ADR-0053).
 *
 * One switch. It is read when the app starts, so the card says plainly when what is wanted is not what is running
 * yet, and which source won (an environment value beats the file). It never claims protection the running process
 * does not give: "active" comes from the backend, not from the switch position.
 */
import { useCallback, useEffect, useState } from "react";
import { Switch } from "@/components/ui/switch";
import { fill, useT } from "@/i18n";
import {
  fetchNetworkPolicy,
  saveNetworkPolicy,
  type NetworkPolicy,
  type NetworkPolicyStatus,
} from "@/lib/networkPolicy";
import {
  SettingsCard,
  SettingsNote,
  SettingsRow,
  SettingsSection,
} from "@/views/settings/SettingsLayout";

export function NetworkPolicyGroup() {
  const t = useT();
  const [status, setStatus] = useState<NetworkPolicyStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setStatus(await fetchNetworkPolicy());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const choose = async (next: boolean) => {
    const policy: NetworkPolicy = next ? "local-only" : "off";
    setSaving(true);
    try {
      setStatus(await saveNetworkPolicy(policy));
      setError(null);
    } catch (err) {
      setError(fill(t("settings_view.network_policy.save_error"), {
        detail: err instanceof Error ? err.message : String(err),
      }));
    } finally {
      setSaving(false);
    }
  };

  return (
    <SettingsSection
      title={t("settings_view.network_policy.section_title")}
      description={t("settings_view.network_policy.section_desc")}
    >
      <SettingsCard>
        <SettingsRow
          testId="network-policy-row"
          title={t("settings_view.network_policy.row_title")}
          description={t("settings_view.network_policy.row_desc")}
          control={
            <Switch
              checked={status?.configured === "local-only"}
              disabled={status === null || saving}
              aria-label={t("settings_view.network_policy.row_title")}
              data-testid="network-policy-switch"
              onCheckedChange={(next) => void choose(next)}
            />
          }
        >
          {status?.env_override && (
            <SettingsNote testId="network-policy-env">
              {fill(t("settings_view.network_policy.env"), { value: status.env_override })}
            </SettingsNote>
          )}
          {status?.active ? (
            <SettingsNote tone="success" testId="network-policy-active">
              {fill(t("settings_view.network_policy.active"), {
                source: status.source,
                count: status.blocked,
              })}
              {status.recent.length > 0 && (
                <div className="mt-1 text-muted-foreground">
                  {fill(t("settings_view.network_policy.recent"), { targets: status.recent.join(", ") })}
                </div>
              )}
            </SettingsNote>
          ) : (
            status && (
              <SettingsNote testId="network-policy-inactive">
                {t("settings_view.network_policy.inactive")}
              </SettingsNote>
            )
          )}
          {status?.restart_required && (
            <SettingsNote tone="warning" testId="network-policy-restart">
              {t("settings_view.network_policy.restart")}
            </SettingsNote>
          )}
          {error && (
            <SettingsNote tone="error" testId="network-policy-error">
              {error}
            </SettingsNote>
          )}
        </SettingsRow>
      </SettingsCard>
    </SettingsSection>
  );
}

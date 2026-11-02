/**
 * The strict offline profile (ADR-0053): the backend contract of
 * `dodaios/ui/web/network_policy_routes.py` and its one writer.
 */

export type NetworkPolicy = "off" | "local-only";

export interface NetworkPolicyStatus {
  /** What the config file says. */
  configured: NetworkPolicy;
  /** A `JARVIS_NETWORK_POLICY` environment value, which wins over the file. */
  env_override: string | null;
  /** Whether THIS process enforces the profile now. */
  active: boolean;
  source: "env" | "config" | "none";
  blocked: number;
  recent: string[];
  /** The guard is installed at start: what is wanted differs from what runs. */
  restart_required: boolean;
}

const URL = "/api/network-policy";

async function readJson<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  return body as T;
}

export async function fetchNetworkPolicy(): Promise<NetworkPolicyStatus> {
  return readJson<NetworkPolicyStatus>(await fetch(URL));
}

export async function saveNetworkPolicy(policy: NetworkPolicy): Promise<NetworkPolicyStatus> {
  return readJson<NetworkPolicyStatus>(
    await fetch(URL, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ policy }),
    }),
  );
}

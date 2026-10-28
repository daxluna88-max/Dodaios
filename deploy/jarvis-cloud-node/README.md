# deploy/jarvis-cloud-node - local, NOT-EXECUTED environment definition (plan block 18)

**Nothing in this directory was built, run, booted, installed, tested or sent to a provider.** No image, container,
VM, service, cloud-init, nftables load, AppArmor load, apt/pip/npm, model download, purchase or account was involved.
These are definition files written by reading the repository and public documentation. Only `bash -n` on the three
shell scripts and a YAML/JSON parse of two data files were done (see checklist).

Design baseline (owner/Dax, no purchase): ONE Linux VPS per customer (x86-64, 4 vCores / 8 GB / 75 GB NVMe, no GPU),
always-on pilot, Ubuntu LTS (24.04 assumed), Xvfb + lightweight WM + session D-Bus + Chromium/Playwright, persistent home,
workspace and browser profile, authenticated HTTPS live view (noVNC) with human takeover, no public VNC/CDP/desktop
port, structured tools first, Chromium sandbox kept, one active browser job, inference keys outside the guest.

## Layout and mapping to ADR-0065

| Path | What | ADR-0065 |
|---|---|---|
| `cloud-init.yaml` | users, packages + apt pin, unattended-upgrades, sshd/fail2ban/sysctl, zram, time sync | 3.1, D14 |
| `systemd/*.service`, `jarvis-agent.slice`, `node.env.sample`, `jarvis-dbus.NOTES.md` | xvfb, dbus, wm, vnc, novnc, runtime, optional browser@ | 3.2, D8, D9 |
| `proxy/Caddyfile.sample` | TLS, auth hook, WebSocket, Origin check | 3.2 gateway, T15 |
| `nftables/jarvis-node.nft` | inbound default-deny, agent-uid egress control | 10, T4 |
| `apparmor/jarvis-chromium`, `chromium-policy/jarvis-managed.json`, `bin/chromium-launch` | sandbox-preserving browser | D9 (revised) |
| `docs/CHROMIUM-LAUNCH-PROFILE.md`, `docs/VOLUME-AND-UPDATES.md`, `bin/jarvis-*.sh` | launch profile, layout, secrets, A/B updates | 3.3, 3.5 |
| `DEPENDENCY-MAP.md` | D1-D20 status and the code gaps | 1 |

## Principals
- `root` + systemd = the supervisor (units, nftables, release activation, credentials). No extra supervisor account.
- `jarvis-ops` = only SSH account (key-only, source-restricted). `jarvis` (uid 2000) = the agent: no sudo, no ssh, not in
  any privileged group. `jarvis-novnc` (to create) = web bridge, separate uid.
- Inference/provider keys never enter the guest; the guest holds one revocable per-customer broker token.

## Key decisions (and what they change versus ADR-0065)
1. Chromium sandbox stays ON; Ubuntu 24.04 userns restriction handled by a per-binary AppArmor `userns,` grant (docs).
2. No disk swap; zram 25% (2 GB) plus per-slice `MemoryMax` so the OOM hits the agent slice, not sshd/Caddy.
3. Egress enforced by nftables on the **agent uid** (metadata/RFC1918/control plane/ports blocked, broker single port),
   with systemd `IPAddress*` as a second layer. Web tier (deny-list vs allow-list) is an open owner decision.
4. Immutable releases (A/B, signed bundle, rollback); no runtime pip.

## NOT verified
Everything functional: whether any unit starts, whether the units' hardening breaks Xvfb/Chromium/websockify, `nft -c`
and `caddy validate` syntax, AppArmor syntax and the `NoNewPrivileges` + profile-transition interaction, the
`python -m dodaios --product consumer serve` command line, `check_product_root` on `/data`, x11vnc flags, noVNC path,
Playwright's policy directory, whether browser-use sets/needs the sandbox flag and its CDP binding, RAM budget
(6 GB agent slice is a guess), apt package availability and names on 24.04, cloud-init schema, provider facts (OVHcloud
metadata/management addresses, port policy, network type), and every claim from memory about kernel/AppArmor behaviour.

## Syntax-read checklist (the only checks allowed in this pass)
- [x] `bash -n bin/chromium-launch bin/jarvis-data-init.sh bin/jarvis-release-activate.sh` (passed, nothing executed)
- [x] YAML parse of `cloud-init.yaml`, JSON parse of `chromium-policy/jarvis-managed.json`
- [ ] read each `systemd/*.service` against `systemd.exec(5)` names (no `systemd-analyze verify`: not run)
- [ ] read `nftables/jarvis-node.nft` (first real step later: `nft -c -f`, throwaway host)
- [ ] read `proxy/Caddyfile.sample` (later: `caddy validate`)
- [ ] confirm no secret, token, real IP or credential: only RFC 5737/3849 documentation ranges and `TODO` markers

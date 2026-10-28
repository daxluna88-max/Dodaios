# Per-customer layout, secrets, logs, immutable updates (DEFINITION - NOT RUN)

## 1. Layout (one VPS = one customer; `bin/jarvis-data-init.sh` creates it)

```
/opt/jarvis/                      root:root 0755   IMMUTABLE for the agent
  releases/<ver>/ venv/ dist/ browsers/ bin/ etc/ RELEASE      (A/B directories)
  current -> releases/<ver>       previous -> releases/<older>
/opt/jarvis-supervisor/           root:root 0755   release-activate, verifier (TODO)
/etc/jarvis/                      root:root 0755   node.env (non-secret) ; broker-token, vncpasswd root:root 0400
/data/                            the customer volume (see section 5 for encryption)
  .node/                          root:root 0755   release pin, customer_id marker (TODO)
  jarvis-consumer/   jarvis 0750  JARVIS_DATA_DIR, claimed by .product {"product":"consumer"} (ADR-0054)
    dodaios.toml  memory/ wiki/ skills/ logs/ outputs/            existing stores
    credentials.json 0600         file credential backend (see section 2)
    society/<agent>/browser-profile/   persistent Chromium profile 0700
  home/        jarvis 0700        HOME: Documents/ Downloads/ .config/ .cache/  (the "PC")
  work/        jarvis 0700        long-task scratch
  cache/       jarvis 0700        browser caches, excluded from backup
```
Rules: one volume mounted by one guest; no other-customer paths exist on the host; the agent has no write
access outside `/data`, `/run/jarvis` and its private `/tmp` (units use `ProtectSystem=strict`).
Discrepancy to fix: ADR-0065 3.5 draws `.secrets/`, but `core/config.py::_FileCredStore` writes
`credentials.json` directly in the data dir (via `_resolve_writable_data_dir()`); this definition follows the code.

## 2. The secrets file: what it holds and what replaces it
`_install_file_cred_backend` keeps a 0600 JSON file; the agent's shell runs as the same uid and can read it.
Therefore in this design **it must not hold inference/provider keys**:
- Provider/engine keys live **outside the guest** in the broker (open decision 3 of ADR-0065, baseline here: broker).
- The only guest credential for inference is a **per-customer broker token**, delivered by root through systemd
  `LoadCredential=` (tmpfs under `/run/credentials/<unit>/`, not in the unit, env or user-data) and read by the runtime
  (gap G1/G2 - not implemented). Blast radius if stolen by prompt injection: that customer's metered budget,
  revocable at the broker.
- Website logins/cookies live in the Chromium profile (customer data, same exposure as the file).
- BYO customer keys, if offered later, are sent to the broker and never stored in the guest.
- Residual/open: runtime and tool shell share one uid. A uid split (`jarvis-rt` for the runtime holding the token,
  `jarvis` for tools) would close the token read; cost: file ownership across the volume. Not designed yet.
Control key (D3): minted per boot by root, written to a credential, shared with Caddy; never shown to the customer.

## 3. Logs without content
- journald: bounded (512M / 14 days), unit stdout only. The app logs under `/data/jarvis-consumer/logs` (content,
  stays on the volume, goes with export/delete). `core/redact.py` is not a guarantee: gap G9 = operator-visible
  log schema allow-list.
- Caddy access log: method/status/size/duration only (uri/headers deleted in the sample).
- nftables drop logs: counters and rate-limited prefix lines, addresses only.
- Nothing is shipped off-node by this definition (shipping target undecided; metadata only when chosen).

## 4. Immutable runtime updates
- Update unit = a **signed release bundle** (tar with `RELEASE`, venv, prebuilt `dist/`, Playwright browsers,
  `bin/`, `etc/`), built in CI (not designed here). `bin/jarvis-release-activate.sh` verifies the signature
  (fail closed if the verifier is missing), unpacks into a fresh `releases/<ver>/` as root, flips `current`
  atomically (`ln -sfn` + `mv -T`), keeps `previous`, reloads the AppArmor profile, restarts the runtime.
  `--rollback` flips back. Signature scheme (minisign/cosign/gpg) = owner decision.
- The control plane takes a verified snapshot of `/data` BEFORE activation (ADR-0065 3.3); a data migration
  that cannot be reverted needs Dax's approval.
- No `pip install`, `uv`, `npm`, `playwright install` or `apt` at runtime by the agent. Code gaps: the Browser-Use
  managed venv is currently built at runtime into `DATA_DIR/society/browser` (`society/browser/install.py`,
  `requirements.lock`) - G5; `dodaios update`/relauncher/marketplace/self_mod must be switched off - G2.
- OS packages: unattended security upgrades only (cloud-init); kernel/libc reboots are supervised, not automatic.

## 5. Volume and encryption (OPEN)
Baseline VPS-2 has one 75 GB NVMe root disk. Options: (a) no extra volume, `/data` is a directory on root,
provider-side encryption unknown; (b) a LUKS file/partition for `/data` unlocked at boot - the key must be present
unattended, so it protects mostly against disk/snapshot theft, not against the provider host; (c) provider block volume.
Owner decides; `cryptsetup` is listed in cloud-init as a TODO package. `jarvis-data-init.sh` refuses to run unless
`/data` is a mount point, so a missing volume never silently lands on the root disk (if (a) is chosen use a bind mount).

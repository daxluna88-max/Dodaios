# ADR-0065 - Jarvis Cloud: runtime, customer isolation, security and service operation (blocks 18, 19, 21)

**Status:** Proposed (design and static audit only) · **Date:** 2026-10-28 · **Related:** ADR-0049, 0050, 0053, 0054,
0058, 0059, 0060, 0061, 0062, 0064, `docs/headless-vps-deployment.md`, `docs/BRANCH-MAP-2026-10-27.md`

**Nothing was run.** No test, build, service, VM, container, provider API call, download, sign-up or spend. No provider
is chosen or activated. Everything below was read in code or reasoned; it is a design reference, not a measurement.
Controls named here are "prepared, NOT RUN" or "to be written". Decisions are left to Dax (section 9).

Status legend used per item: **designed** (specified here, not built), **partial** (code exists, not run, gaps named),
**open** (needs a decision or fact not available).

## 0. What changed since ADR-0059

ADR-0059 proposed a text-first hosted offer with no tools that execute code in the base plan. The owner's new
requirements (2026-10-28) replace that scope:

1. The hosted assistant must work **like a physical PC**: create folders and files, run a **real browser with a
   persistent logged-in profile**, a shell, GUI automation (computer use), and long tasks. Each customer therefore
   needs a **full per-customer Linux desktop-like environment** (virtual display, Chromium, file system, secret store
   replacing the OS keyring), not an API sandbox.
2. It must **cost little**: most customers are idle most of the time, so design for hibernate / snapshot /
   scale-to-zero and density, while keeping strong isolation.
3. **Provider choice is not made.** Another set of agents compiles the provider list. This ADR stays provider-neutral
   and ends with per-architecture requirements (Appendix A) so Dax can match them.

Consequence for ADR-0059 section 3: the "text-only base plan" mitigation no longer applies. The hardest problem
(running model-driven code and a browser for strangers) is now the core, so isolation is stricter, not looser.

---

# BLOCK 18 - Runtime server

## 1. Static dependency audit (read, not run)

Counts are files under `dodaios/` matching the pattern (a rough size of the surface, not a defect count):
keyring 86, pystray/tkinter 9, pyaudio/sounddevice 24, `Path.home`/`expanduser` 105, pynput/hotkey 142,
webview/desktop_app 70, screenshot/input libs 50, `platform.system`/`sys.platform` 226, `127.0.0.1`/`localhost` 94,
Windows/macOS native APIs 89, playwright/chromium 12, subprocess 225.

Good news first: the project already has a **headless profile** (`Dockerfile`, `--headless`, `jarvis serve`,
`docs/headless-vps-deployment.md`), a **file credential fallback** (`core/config.py::_install_file_cred_backend`,
0600 file when no OS keyring), a **browser-microphone voice bridge** (`dodaios/browser_voice/`, int16 PCM over
WebSocket, "NEVER sounddevice"), a **virtual X display provider** (`dodaios/agent_screen/providers/xvfb.py`: Xvfb +
window manager + isolated XTest input), a **persistent per-agent Chromium profile** (`dodaios/society/browser/session.py`:
`DATA_DIR/society/<agent_id>/browser-profile`, headless after one headed login), and a **fail-closed bind guard**
(`assert_bind_safe`: non-loopback needs a control key). The gaps are mostly multi-tenancy and remote access, not
desktop removal.

| # | Dependency | Where (read) | Needed in cloud? | Replacement | Effort |
|---|---|---|---|---|---|
| D1 | OS keyring for provider keys, control key, tokens | `core/config.py` (`get_secret`, `_ensure_keyring_backend`), `core/control_key.py`, `core/identity_credentials.py`, `core/secret_slots.py`, `marketplace/token_store.py`, `computers/*`, `friends/identity.py`, `plugins/stt/*` | Yes | Per-customer secret store behind the same `get_secret/set_secret` seam: (a) the existing 0600 file backend on an encrypted per-customer volume as the floor; (b) preferred: a keyring backend that reads from a node-local agent holding a per-customer key (section 5/11). No shared vault, no env vars in unit files for customer secrets. | M |
| D2 | `~/.dodaios-data`, `Path.home()`, `expanduser` literals | `core/paths.py` (`user_data_dir`, products), 105 files | Yes (must be the volume) | Reuse `--product` style root (ADR-0054): `JARVIS_DATA_DIR=/data/jarvis-consumer`, `JARVIS_PRODUCT_ACTIVE=consumer`, `HOME=/data/home` on the customer volume. `check_product_root` forbids links and overlaps, which fits a mounted volume. Audit the ~105 `home()` uses for anything outside `HOME`. | S-M |
| D3 | Single owner, per-user Bearer Control Key | `core/control_key.py`, `ui/web/surface_security.py` | Inside the VM yes; as the internet auth no | The Control Key stays a node-internal credential between the gateway and the instance. Internet auth (accounts, sessions, recovery, rate limits) is a new control-plane component, not this key (section 8 of ADR-0059; block 19 here). | L (new) |
| D4 | Loopback assumptions, `127.0.0.1` ports 47821/47822/9090 (+2000 Consumer), `JARVIS_BIND_HOST` | `ui/web/launcher.py`, `core/instance.py`, `surface_security.py` (Host/Origin/CSRF) | Yes, but never public | Keep loopback inside the instance; a node-local gateway reaches it over a per-VM private channel (vsock / private tap / unix socket), terminates TLS and the account session, rewrites Host/Origin. Host/Origin checks must be configured for the public hostname, not disabled. | M |
| D5 | Desktop window (pywebview/Qt), tray, overlay, drop target, single-instance shell | `ui/desktop_app.py`, `ui/tray.py`, `overlay/*`, `ui/dodaiosbar/*`, `ui/shell/*`, `ui/tk_runtime.py` | No | Do not install the `[desktop]` extra. The React UI is served by the backend to the customer's own browser/phone. The "agent sees its own virtual desktop" is a separate thing (D8). | S (exclude) |
| D6 | Local audio devices (sounddevice/PortAudio), wake word, local STT | `audio/*`, `speech/*`, `telephony/*` | No (device) | Browser/phone microphone via `browser_voice` (WebSocket PCM) and cloud STT/TTS; later WebRTC for echo cancellation and jitter. Wake word runs on the client or is dropped. Metering on the server. `libportaudio2` stays only so imports succeed. | M (WebRTC later) |
| D7 | Global hotkeys, pynput, macOS/Windows native input and window APIs | `trigger/backends/*`, `platform/*` (89 native-API files), `cu/actuate` | No on the host; yes against the virtual display | Hotkeys: none (UI buttons). Input to the virtual display via XTest (pynput on X11 is already the Linux path in `docs/computer-use.md`). | S |
| D8 | Computer use / screen capture (`mss`, pyautogui-style, AT-SPI tree) | `vision/*`, `cu/*`, `agent_screen/*`; `cu/direct.py::readiness` returns `headless` / `wayland` | **Yes, core requirement** | Run a per-customer **Xvfb + lightweight WM** (the `xvfb` agent-screen provider already does this); capture via `mss` against that display; AT-SPI needs a session D-Bus (add `dbus-daemon --session` to the node image). Use X11 not Wayland (readiness refuses Wayland). Optional live view for the customer: VNC/noVNC-style stream through the gateway, auth required (open). | M |
| D9 | Browser: Playwright Chromium + persistent profile; attach-to-user's-Chrome (CDP) mode | `society/browser/{session,install,runner,chrome,manual_chrome}.py` | Yes, core | Pre-bake Chromium in the read-only base image (`PLAYWRIGHT_BROWSERS_PATH` read-only), profile on the customer volume. **Remove/disable `attach` mode and `manual_chrome`** (they drive the person's own Chrome). First login (headed) is done through the live view (D8) or an assisted cookie import (open). The Chromium sandbox needs unprivileged user namespaces or `--no-sandbox`; inside a microVM with its own kernel `--no-sandbox` is acceptable only because the VM is the boundary (section 3). | M |
| D10 | Shell / terminal / PTY host, agentic IDE, CLI runtimes, `run_shell`, MCP stdio servers | `terminal/*`, `agentic_ide/*`, `safety/tool_executor.py`, `mcp/*` | Yes | Allowed inside the customer environment as an unprivileged user; containment is by the VM + egress policy, not by the app (the app guard, ADR-0053/0060, is not a sandbox). | S |
| D11 | Vendor CLI subscription logins (Claude/Codex) | `agent_runtimes/*`, `clis/*`, `codex_app_server.py` | Open (terms) | Not assumed (ADR-0059 section 2). Cloud path uses customer API keys or operator-metered engines. Codex login has a Wayland/no-DISPLAY branch (`codex_app_server.py:1554`). | open |
| D12 | Egress guard (Python socket patch) | `core/egress_guard.py`, ADR-0053/0060 | Not as a security boundary | Useful as defence in depth only. Real egress control is outside the VM (section 12). ADR-0060 says plainly that it is not enforcement. | none |
| D13 | Locks: per-product launch lock, `running_products()`, single-instance | `core/product_profile.py` | Per instance only | Keep: it prevents a second runtime on one volume (useful as a fence against double-mount). | none |
| D14 | Update/relaunch/self-update, installer, `dodaios update`, autostart | `ui/relauncher.py`, `app_update`, `autostart/*`, `install/*` | **No - must be off** | Disable in-instance self-update (the image is immutable; update = replace image, section 3.3). A customer or a tool must not be able to rewrite the install. | S (flag) |
| D15 | Self-modification and plugin/marketplace install | `core/self_mod/*`, `marketplace/*` | Policy decision | Marketplace tools run arbitrary code with the customer's secrets. Default off or allow-list; open decision. | open |
| D16 | SQLite stores (board, memory, usage, wiki index) | `core/paths.py`, `board-backend`, `memory/*` | Yes | One writer process per volume makes SQLite fine. Backups use the online backup API as in ADR-0064 H5 (`core/product_backup.py`); extend to sub-directories (wiki, skills, browser profile) which ADR-0064 lists as not covered. | M |
| D17 | Logs with content | `user_logs_dir()`, `core/redact.py` | Yes | Per-customer logs stay on the customer volume; operator-wide logs get metadata only (section 13). | M |
| D18 | Telemetry/metrics (`telemetry.metrics_port` 9090+2000), update checks, phone pairing | `core/instance.py`, `phone/*` (ADR-0062) | Metrics yes (internal) | Metrics scraped over the private channel, never public. Phone pairing (LAN-oriented) is replaced by account sessions; keep the device-revocation logic. | M |
| D19 | Desktop Output mirror, Windows junctions, macOS/Windows packaging | `core/paths.py` mirror, `packaging/*` | No | Skip. | none |
| D20 | Process-wide singletons holding customer state (STOP controller, kill switch, turn ledger, style runtime) | ADR-0059 section 2 | Fine in per-customer instances; blocking in a shared runtime | This is the central cost of architecture B (section 2 below). | L in B, none in A |

Findings: (1) no hard blocker to a headless Linux runtime per customer; most desktop code is already optional.
(2) The real new work is the **control plane** (accounts, routing, wake/sleep, metering, secrets distribution) and
the **environment image**, not rewriting the agent. (3) The `attach`/own-Chrome modes, in-instance self-update and
marketplace install are dangerous in a hosted context and must be switched off by configuration of the node image.
(4) Wayland, `cu` readiness `headless`, and AT-SPI-without-D-Bus are the likely first runtime failures of computer use
and are cheap to prevent with Xvfb + session bus. (5) Nothing in this table was executed.

## 2. One instance per customer vs shared multi-tenant runtime (ANALYSIS - decision left to Dax)

Both candidates are for a **PC-like** agent: shell, browser and GUI run arbitrary, model-driven code for strangers.

| Criterion | A. Instance per customer (VM / microVM + persistent volume) | B. Shared multi-tenant runtime (processes/containers on a shared kernel) |
|---|---|---|
| Isolation | Hardware-virtualization boundary and a separate guest kernel; a kernel exploit from the browser/shell must also escape the hypervisor. Strongest practical for untrusted code. | One kernel shared. A browser or shell exploit (frequent class: Chromium renderer + kernel LPE) lands on the host with other tenants. Needs gVisor/Kata-style hardening on top, which is itself approaching A. Plus app-level tenant separation across ~20 singletons (D20). |
| Fit with today's code | Closest: the runtime is one-owner; `--product` data root maps to the volume 1:1. | Large refactor (tenant id on every path, query, cache, job). Not credible for a PC-like toolset. |
| Density / idle cost | Idle VMs cost RAM unless snapshotted. With **snapshot-to-disk + scale-to-zero** idle cost falls to storage only; wake latency is the price (seconds to tens of seconds, not measured). A microVM can have a small footprint (order of tens of MB base plus guest workload). | Best density on paper, but a Chromium + Xvfb per tenant is the real memory cost either way (hundreds of MB), so B saves less than expected for the desktop part. |
| Cost at active use | Per-active-customer CPU/RAM (browser dominates); bounded by quotas. | Similar for browser; saves only the guest OS overhead. |
| Ops burden | Image build, snapshot lifecycle, wake/sleep orchestrator, volume management, per-VM networking. Needs a node agent and a placement scheduler. | One fleet to patch, but every bug is a cross-tenant incident. |
| Blast radius | One customer (VM), assuming no shared writable layer and no host mounts. | All tenants on the host. |
| Update model | Immutable image; roll forward by new image on next wake (customers are updated lazily); rollback = previous image; stale images are a patch-lag risk to monitor. | Fast fleet-wide, but one bad deploy hits all. |
| Backup/export/delete | Per volume: trivial to snapshot, export, and crypto-erase (destroy the key). | Per-tenant slicing of shared stores. |
| Noisy neighbour | Hard cgroup/VM limits per VM. | Soft limits; shared kernel resources. |

**ANALYSIS / recommendation (not a decision):** use **A** for everything that executes tools (shell, browser,
desktop, long tasks): one hardware-isolated environment per customer with a persistent volume and
**snapshot/scale-to-zero when idle**. Keep a **shared control plane** (accounts, billing/ledger, routing, wake/sleep
orchestrator, secrets broker, metering, abuse) that holds **no customer content** and never executes customer code.
Light chat-only turns (no tools) may later be served by a shared stateless "thin brain" path to avoid waking a VM -
that is an optimization with its own tenant-isolation burden and is **open**. Containers sharing a kernel are
acceptable only as the *inside* of a VM, never as the customer boundary (consistent with ADR-0059 section 3).

Cost levers (all unmeasured; no prices invented): idle -> snapshot to disk and stop the VM (pay storage only);
keep the browser lazily started (it is the dominant RAM consumer); thin base image shared read-only across VMs
(page-cache sharing on the host); per-customer volume sized small by default and billed above a cap; wake only on
a real trigger (user message, scheduled job, webhook); long tasks hold the VM awake and are budgeted; cap
concurrent awake VMs per node and queue wake-ups. The 25-30 EUR/customer target of ADR-0059 is unvalidated and
depends on idle ratio, awake hours per customer, and the browser memory footprint; measure before promising.

## 3. Minimal "Jarvis Cloud node" image spec

Terminology: **node** = a physical/virtual host running the hypervisor and node agent. **Customer environment** =
the guest (microVM or VM) booted from the **base image** plus the customer's persistent volume. Which hypervisor or
sandbox technology is **open**; the requirements are in Appendix A.

### 3.1 Components of the guest base image (read-only root)

- Minimal Linux userspace, Python 3.11+ (the project's headless install: no torch, no GPU, no Node at runtime;
  prebuilt `dist/` UI), Dodaios installed from a pinned wheel/lockfile (`uv.lock` / `requirements.txt` with hashes).
- Chromium (Playwright build or distro) pre-installed read-only; fonts; `Xvfb`, a tiny WM (`openbox`), `dbus` session
  daemon, `xdotool`/XTest libs, optional `x11vnc`-style streamer for the customer's live view.
- Shell tooling a PC user expects (git, curl, coreutils, python venv, node optional), no compilers by default
  (open: allow `apt`-style installs into the volume overlay?).
- A tiny **guest agent** (supervisor + health + secret receiver + vsock/private-channel listener). It is the only
  thing the node talks to.
- No SSH server, no cloud-init user data with secrets, no package-manager credentials, no operator keys baked in.
- Unprivileged user `jarvis` (uid fixed), all tools run as that user; root only for the guest agent; no sudo.
- Hardening: read-only root, `tmpfs` for `/tmp` and `/run`, `noexec,nosuid,nodev` where compatible (browser cache
  may need exec; test), seccomp default profile for the browser (Chromium's own) and a restrictive profile for the
  tool runner, AppArmor/SELinux profile for the Dodaios process (as a second layer inside the guest), `no_new_privs`,
  dropped capabilities, cgroup v2 limits (memory, CPU, pids, io), no `/proc/sys` writes.

### 3.2 Processes (inside the guest) and ports

| Process | Role | Listens |
|---|---|---|
| guest agent (PID 1 child / supervisor) | start order, restart policy, health, graceful shutdown, secret hand-over, STOP hook | guest-private channel only (vsock or private tap); **no TCP port reachable from outside the node** |
| `python -m dodaios --product consumer ...` (headless) | the Jarvis runtime | `127.0.0.1:49821` (admin), `49822` (MCP http), `11090` (metrics) - ADR-0054 offsets, loopback |
| Xvfb `:90..` + openbox + dbus session | virtual desktop | unix socket only (`-nolisten tcp`) |
| Chromium (Playwright-managed) | the real browser | no remote debugging port exposed except over loopback to the runtime; never to the network |
| optional live-view streamer | customer sees/takes over the screen | unix socket to guest agent; exposed only via the gateway after auth |

**Ports public: none.** No guest port is on the internet. The node-local **gateway** (a component of the control
plane, outside the guest) terminates TLS, validates the account session, and proxies to the guest agent over the
private channel. Guests cannot reach each other (no shared L2; default-deny between guests).

### 3.3 Update and rollback

- The base image is **immutable and versioned** (content-addressed). Update = publish new image, mark it
  `candidate`, canary on staff-owned environments, then roll customers **lazily at next wake** (the boot picks the
  current approved image; an environment can be pinned to the previous one).
- Per-customer **schema migrations** run at first boot on the volume after an **automatic verified pre-migration
  snapshot** (ADR-0064 H5/H6 lessons: coherent SQLite backup, refuse to migrate if the backup failed, manifest with
  hashes). Rollback = boot previous image on the pre-migration snapshot. A migration that cannot be reverted is
  flagged and needs Dax's approval.
- In-guest self-update, marketplace install and `dodaios update` are disabled (D14, D15).
- Emergency: a kill-list of image digests the gateway refuses to wake (patch a critical CVE by forcing the new image).
- Status: **designed**, nothing built. The ADR-0064 gaps (frozen rollback) do not apply to a server image, but the
  data-backup coverage gap (sub-directories not backed up) does and must be closed first.

### 3.4 Health

Guest `GET /api/health` already exists (`ui/web/server.py:1241`, allowed unauthenticated by `surface_security`),
used by the Dockerfile `HEALTHCHECK`. The guest agent adds: process liveness (runtime, Xvfb, WM, Chromium on demand),
disk free on the volume, memory pressure, last-activity timestamp (feeds the idle-sleep decision), schema version,
image digest. The node reports only these metadata to the control plane, never content.

### 3.5 Per-customer volume layout (reuse the `--product` root, ADR-0054)

```
/data/                      # per-customer encrypted volume (one volume = one customer)
  .product                  # ownership marker "consumer" (ADR-0054)
  jarvis-consumer/          # JARVIS_DATA_DIR, name must end in -consumer (check_product_root)
    dodaios.toml            # JARVIS_CONFIG
    memory/ wiki/ skills/ logs/ phone/ outputs/   # existing stores
    society/<agent>/browser-profile/              # persistent Chromium login (D9)
    .secrets/               # 0600 file credential store (D1) - encrypted by the volume key
    .backup/                # NOT counted as a backup (ADR-0064: it shares the disk)
  home/                     # HOME of user jarvis: the "PC": Documents, Downloads, projects, shell history
  work/                     # long-task scratch
```

Rules: one volume is mounted by exactly one guest at a time (fence); the volume holds **no** operator material;
the base image never writes to it except through the runtime's own paths; the customer "PC" files in `home/` are
part of export/backup scope (ADR-0064 covered only top-level product files, so this is new work).

---

# BLOCK 19 - Account and customer isolation

## 4. Customer identity across every surface

Define `customer_id` (opaque, random, non-guessable, never reused, not an email) minted by the control plane. It is
bound at each layer by construction (separate resource), not by a filter in shared code:

| Surface | How `customer_id` binds | Isolation form | State |
|---|---|---|---|
| Memory (wiki, notebooks, indexes) | inside the customer volume | separate volume + separate VM | designed |
| Files / "PC" home, uploads, outputs | same volume | same | designed |
| Credentials (provider keys, browser logins, control key) | volume-local secret store; control-plane stores only a wrapped key reference | separate volume key | designed |
| Jobs, approvals, STOP state | inside the instance; control plane keeps a job *index* of ids/status only | per instance; STOP scope = the customer | designed (reuses ADR-0058 within one instance) |
| Logs | content logs on volume; operator logs metadata only, keyed by `customer_id` | split by design (section 13) | designed |
| Backups / snapshots | per-customer snapshot object, encrypted under the customer key, `customer_id` in the manifest and verified at restore (ADR-0054 marker + ADR-0064 manifest check) | per-customer object | designed |
| Ledger/billing | control plane DB, row-scoped, no content | row-level + audit | designed |
| Control-plane DB | tenant column + row-level security; holds identities, quotas, ledger, session records - no memory/file content | DB-level | designed |

Cross-checks (prepared, **NOT RUN**, to be written): a volume carrying another customer's marker refuses to mount;
a snapshot whose manifest `customer_id` differs refuses to restore; a gateway request whose session `customer_id`
differs from the target guest is dropped; a canary-token test places a unique string in customer A's stores and
searches every operator-visible surface and customer B's environment for it.

## 5. Sessions, revocation, STOP

- **Account session:** short-lived access token + rotating refresh token bound to a device record; server-side
  session table in the control plane; each device listed to the customer and individually revocable (reuse the
  logic of ADR-0062 device revocation: re-check at each mutation, not only at login).
- **Revocation is not STOP.** Revoking a device ends its ability to send commands; an agent already running keeps
  running (ADR-0059 section 4, ADR-0062 residual). Offer both: "Sign out this device" and "Sign out everywhere and
  stop everything" (revoke all sessions + remote STOP, section 14).
- **Step-up** for sensitive actions (export, delete account, change recovery, reveal secrets, raise budget):
  re-authentication (passkey/TOTP). Status: designed.
- Rotate the node-internal Control Key per boot (the gateway injects it via the guest agent; never shown to the
  customer; D3).

## 6. Account recovery without weakening isolation

Principle: recovery restores **access to the same isolated environment**; it never creates a path for the operator
or another account to read it.
- Recovery factors chosen at signup: passkey(s) + a one-time **recovery code set** shown once, and/or a second
  verified channel. Email-only recovery makes the email provider the root of trust (the attacker who owns the mailbox
  owns the PC): flag this to the customer; require a waiting period (e.g. 48-72 h, with notifications to all
  devices/channels and a cancel link) for recovery that has no strong factor.
- **Per-customer encryption keys and recovery (the hard trade-off):**
  - Mode 1 *operator-wrapped key (default for a low-cost consumer product)*: the volume key is wrapped by a
    control-plane KMS key; recovery is possible after identity re-verification. Operator **can** technically decrypt
    (section 8).
  - Mode 2 *customer-held key*: the key is derived from/wrapped by the customer's passkey or passphrase; lose it and
    the data is **unrecoverable by design**. Constraint: the environment must run unattended (long tasks, scheduled
    jobs) so the key must be present in the running guest's memory; the operator who controls the hypervisor can
    still read guest memory while it runs. So Mode 2 protects data **at rest** and against some insiders, not against
    a compromised or malicious host during execution.
  - Status: **open** (Dax: which mode, or tiers).
- Account takeover mitigations: login notifications, anomaly-based step-up, no security question, rate-limited and
  uniform recovery responses (no account enumeration), recovery never reveals stored secrets (provider keys are
  write-only in the UI after saving).
- A recovery by operator staff on behalf of the customer is **not offered**; support can only trigger the
  automated flow.

## 7. Operator access and break-glass

Default: **operators have no interactive access to a customer environment and no standing credentials to customer
volumes.** What operators can see by default: metadata (state, sizes, image digest, health, ledger totals, error
codes).
- **Break-glass** (support or incident): requires (a) a customer-initiated or customer-consented case, or a defined
  legal/safety exception; (b) two-person approval; (c) time-boxed (e.g. 60 min) just-in-time credentials; (d) scope
  declared (logs only / file listing / screen view / full); (e) session recorded; (f) an **audit entry written to an
  append-only store outside operator control and shown to the customer** in their account ("Support accessed X on
  date, reason, duration") - customer-visible, including for security-driven access when legally allowed to disclose.
- Prefer **customer-driven support**: the customer clicks "allow support to view for 30 minutes" in-app.
- Operators cannot read secrets: provider keys are write-only; break-glass sessions run as a separate non-privileged
  view, and secret files are excluded from the view unless the scope explicitly says so.
- Status: designed. The audit store, the JIT access mechanism and the customer-visible log are **to be built**.

## 8. Encryption at rest and what it does NOT imply

- Per-customer volume encryption with a per-customer data key (envelope: DEK per volume, wrapped by KEK in a KMS or
  hardware module owned by the operator). Also encrypt snapshots/backups under the same hierarchy. Crypto-erase =
  destroy the wrapped DEK (strong deletion for live data and snapshots under that key).
- **Encryption at rest does NOT mean the operator cannot read the data.** While the environment runs, plaintext
  exists in guest memory and the hypervisor/host can read it; with an operator-held KEK, the operator can also
  unwrap the key. Only a customer-held key (Mode 2) removes the second, and none of the modes protect against a
  malicious host during execution (confidential computing - SEV-SNP/TDX-class - narrows this but is a provider
  requirement and an **open** option, not assumed).
- The data also leaves the environment to the **engine vendors** the customer's turns use (ADR-0050 privacy gate);
  at-rest encryption is irrelevant to that flow.
- Product text must never say "only you can read your data" for Mode 1 (ADR-0059 section 4 already forbids such a
  promise).

## 9-bis. Export, deletion, GDPR roles

- **Export:** one archive of the volume's customer-scope content (memory pages as Markdown, files, `home/`, settings
  without secrets, job history) plus a machine-readable manifest; secrets are not exported in clear (customer re-enters
  or exports an encrypted bundle on request). Produced inside the environment, delivered via a time-limited signed
  link, encrypted to the customer. Limits stated: browser profiles/logins may not be portable; third-party engine
  vendors' own copies are out of scope; export of what the agent *did* elsewhere (sent emails) is not possible.
- **Deletion:** account deletion = revoke sessions, STOP, crypto-erase (destroy DEK) and delete volume, snapshots and
  control-plane rows except those required by law (billing records, abuse evidence for a stated period).
  What remains and for how long (provider-side backups of the host, replicated storage, logs rotation, vendor engine
  retention) is **stated to the customer**; do not call "archived" deleted (ADR-0058, ADR-0061 deletion matrix model
  - residual list per store). Deletion completeness is a test to write (NOT RUN).
- **GDPR roles (analysis, not legal advice; counsel to confirm):** for the content in the customer's PC and memory,
  the customer is typically the **controller** (or a household user outside GDPR scope) and the operator a
  **processor**; for account, billing, abuse and service-security data, the operator is a **controller**. Engine and
  hosting vendors are **sub-processors** of the operator. Needs: privacy notice, record of processing, a **DPA**
  offered to customers who are controllers (business users) and **DPAs with every sub-processor** (host, engine
  vendors, payment, email, monitoring), a transfer assessment if any sub-processor is outside the EU/EEA, a DPIA
  screening (new tech, automated agent acting on third-party sites), breach-notification procedure (72 h to the
  authority), and a lawful basis/limit for operator access (section 7). A customer running the agent on
  **third parties' personal data** (emails, contacts) makes them a controller with their own duties; the ToS must say so.
  Status: **open** (legal review needed before any customer; the ADR-0059 note stands).

## 9. Threat model (customer isolation)

| # | Threat | Actor | Impact | Control (prepared, NOT RUN) | Residual | State |
|---|---|---|---|---|---|---|
| T1 | Browser/renderer exploit -> guest kernel LPE -> escape | malicious page, prompt-injected agent | access to host/other tenants | microVM boundary, minimal guest kernel, KVM isolation, host patch cadence, jailer/seccomp on the VMM, no shared FS | hypervisor 0-day | designed |
| T2 | Cross-tenant data access via shared storage/network | tenant, bug | leak | per-customer volume + key; no shared writable layer; default-deny guest-to-guest | storage backend bug | designed |
| T3 | Prompt injection makes the agent exfiltrate secrets/files | web page, email, document | data theft | egress allow-list; secrets not in env; write-only keys; approval gate (ADR-0058/0062) for sensitive tools; privacy gate (ADR-0050) toward engines | model follows injected instruction within allowed destinations | partial |
| T4 | SSRF to cloud metadata / internal services | agent, tool | credential theft | block link-local `169.254.169.254`/`fd00:ec2::254` and private ranges at the node firewall; no instance credentials on the node reachable from guest; metadata service disabled for guests | provider-specific endpoints (must be listed per provider) | designed |
| T5 | Operator insider reads customer data | staff | privacy breach | section 7 break-glass, JIT, audit visible to customer, no standing access, separation of duties | Mode 1 key wrap; host memory access | designed |
| T6 | Control plane compromise | external attacker | mass impact: sessions, routing, key unwrap | KMS separate from control-plane compute; control plane holds no content; secret broker rate limits; admin MFA; blast-radius split (auth service, orchestrator, ledger separate roles) | KEK compromise | designed |
| T7 | Account takeover | attacker | full PC access | passkeys, step-up, recovery delay, session list, anomaly alerts | email-only recovery weakness | designed |
| T8 | Resource abuse (crypto-mining, DoS, spam) | customer | cost, reputation | quotas, egress rate limits, CPU credits, detection (section 12) | evasive low-rate abuse | designed |
| T9 | Snapshot/backup leak or cross-restore | operator error | leak or wrong-tenant restore | per-customer key, manifest `customer_id` check, restore verified before attach (ADR-0064 H6 lessons) | none known | designed |
| T10 | Stale image with known CVE | supply chain | exploit | image digest kill-list, forced upgrade at wake, scanning in CI | lag for sleeping VMs | designed |
| T11 | Logs/telemetry leak content | operator tooling | privacy breach | content-free operator logs, redaction (`core/redact.py`), schema review | accidental PII in error text | partial |
| T12 | Double-mount / split brain of a volume | orchestrator bug | corruption, two writers | volume lease/fence + product launch lock (`product_profile`) | lease service failure | designed |
| T13 | Vendor subprocessor breach | vendor | leak of prompts | minimization (ADR-0050), DPAs, per-vendor disclosure | out of our control | open (legal) |
| T14 | Customer-vs-customer via shared engine account | design error | cross-bleed of cache/memory | no shared conversational state at engine; per-customer keys or per-request isolation; no engine-side "memory" shared | vendor caching semantics (to verify) | open |
| T15 | Live-view / remote-desktop hijack | attacker | session takeover | gateway auth, one-time stream tokens, no direct port | token theft | designed |
| T16 | Hibernation snapshot contains secrets in RAM | operator | secret exposure | snapshot encrypted under the customer key; memory snapshots (if used) treated as most sensitive | RAM image includes decrypted data | open (see 11.3) |

---

# BLOCK 21 - Security and service operation

## 10. Tool isolation inside the customer environment

The browser and shell run arbitrary code (including code the model writes after reading a hostile page). The
**environment is assumed compromised at all times**; what is protected is everything outside it.
- Boundary: the VM (section 2). Inside: unprivileged user, read-only base, cgroup limits (memory, CPU, pids, IO),
  seccomp/AppArmor (3.1), `no_new_privs`, no raw sockets, no `CAP_NET_ADMIN`, no nested containers by default, no
  `/dev` beyond basic, no GPU, no USB, no host mounts, no 9p/virtiofs shares.
- **Egress: default-deny at the node firewall (outside the guest),** not in the guest and not via the Python guard
  (ADR-0060: the guard is not enforcement). Allow-list by destination category (engine vendors, package registries,
  chosen sites) via a **forward proxy** (SNI/Host allow-list with logging of destination only) for HTTP(S); DNS via a
  node resolver that logs/filters; raw TCP/UDP otherwise denied. The "PC-like browsing" requirement makes a pure
  allow-list unfriendly (customers browse anywhere). Therefore two policy tiers, **open for Dax**: (a) *open web via
  proxy* with deny-lists (private ranges, metadata, known-bad, mail ports 25/465/587 blocked, rate limits) - friendly,
  weaker; (b) *allow-list* - strong, limited. Whatever the tier, always block: link-local/metadata, RFC1918 and other
  guests, the node's own management addresses, SMTP direct send, IRC/known-C2 ports, SSH out (or rate-limited).
- **Lateral movement:** per-guest network namespace/tap, no L2 between guests, no shared bridge with ARP visibility,
  anti-spoofing on the tap; guests cannot reach the control plane except the narrow guest-agent channel.
- **Metadata endpoints:** none exposed to guests; credentials for the node are never on the guest path; verify with a
  probe (to write, NOT RUN) from inside a guest to the link-local addresses and the node IPs.
- **Resource limits:** per-VM vCPU/RAM/disk/IO/pids caps, bandwidth shaping, per-customer concurrency, max wall-time
  for tasks unless the customer opts in, disk quotas on the volume with a hard stop.
- Secrets exposure inside the guest: provider keys are delivered to the runtime's secret store (D1), not into the
  browser/shell environment; tool subprocesses get a scrubbed env (project already has `mcp.env_isolation`,
  `child_identity.forward_identity` - reuse), but a shell tool running as the same user could read the 0600 file; so
  **engine API keys should preferably never be in the guest at all**: the node proxy injects them (engine calls go
  through a metering proxy that holds the key; the guest holds only a per-customer token). This is a significant
  design option (**open**) that also gives the per-customer ledger enforcement (ADR-0059 section 6) outside the
  guest's reach. Customer-supplied (BYO) keys follow the same path, stored in the broker.

## 11. Service operation

### 11.1 Abuse handling

- **Prohibited-use policy (ToS/AUP, to write; legal review):** spam/bulk messaging, scraping against site terms,
  credential stuffing, attacks/scanning, malware, illegal content, mining, harassment, evasion of other services'
  anti-bot where it violates law or contract. Customers are responsible for what their agent does (and the agent acts
  with the customer's logins).
- **Controls before the fact:** rate limits per customer (outbound messages, HTTP requests/min, distinct hosts/hour,
  browser sessions), SMTP blocked, signup friction (payment method, verified contact), new-account throttles, resource
  caps. Detection signals (metadata only): egress volume and fan-out, port-scan patterns, high bounce/complaint
  reports from abuse mailbox, CPU patterns, repeated captcha solving, provider abuse notices. No content inspection by
  default; any content review needs the section 7 process.
- **Suspension procedure:** graded - (1) throttle; (2) pause egress (environment keeps running locally); (3) remote
  STOP + sleep (section 14); (4) suspend the account (read-only export remains available for a stated period unless
  illegal content); (5) terminate. Each step records reason, actor, time, customer notice (unless law/safety prevents),
  appeal channel. Evidence preserved minimal and legally scoped. An abuse contact and takedown process must exist
  (host provider AUP often requires one).
- Status: designed; thresholds unmeasured.

### 11.2 Backup / restore (ADR-0064 lessons)

- **Coherent SQLite snapshots:** use the SQLite online backup API per database (ADR-0064 H5), not a file copy of a
  live DB; quiesce the runtime (or stop writes) for a crash-consistent volume snapshot, or take an application-level
  snapshot first and then a block-level one. Sleep/hibernate snapshots are taken after a graceful runtime stop where
  possible (cheap, coherent).
- **Cover the whole customer scope:** ADR-0064 says sub-directories (wiki, skills, logs) are not covered and the backup
  lives on the same disk; here the backup is **off-volume, encrypted per customer, in a different failure domain** and
  covers `home/`, browser profile, memory. Retention stated to the customer (and in deletion limits).
- **Verified restore:** manifest with hashes and `customer_id`/product marker, restore into a quarantine volume, run the
  integrity checks (SQLite `PRAGMA integrity_check`, manifest hashes, marker), only then attach; staged + journalled as
  `restore_product_state`; periodic automated **restore drills** on staff-owned environments. A backup never tested is
  recorded as untested.
- Status: partial (code for single-product desktop in `core/product_backup.py`, not run); server design only.

### 11.3 Hibernate / scale-to-zero (cost) and its security cost

Options (technology-neutral): (i) *stop + keep volume* (cold boot of the guest, restore runtime state from files; seconds
to a minute; simplest; browser tabs lost, profile kept); (ii) *VM memory snapshot + restore* (fast resume, tabs
and shell state kept; memory snapshot holds decrypted secrets and must be encrypted, versioned against image
changes, and is not portable across hosts/CPU models in some technologies - provider requirement); (iii) *keep warm
for N minutes after activity*. Security notes: a restored memory snapshot resumes with the **same RNG/state** -
re-seed entropy and re-establish sessions/time on resume; never resume the same snapshot into two VMs (clone risk);
snapshots are single-use leases. Preferred default: (i) plus warm window, with (ii) as an optimization where the
host supports it. Scheduled jobs and webhooks wake the environment via the orchestrator. Status: designed.

### 11.4 Network control, logging, monitoring

- **Logging without content:** operator-wide logs carry `customer_id`, timestamps, event codes, byte counts, durations,
  image digest, error classes - never prompts, file names, URLs with query, page contents, email addresses of third
  parties. Egress logs: destination host/IP and bytes only (no URL path, no payload). Content logs live on the
  customer volume and are exported/deleted with it. Retention fixed and short for operator logs. `core/redact.py` is a
  starting point; a schema allow-list for operator log fields is safer than redaction (to write).
- **Monitoring:** node health, wake latency, awake-VM count, snapshot age, backup success/verification, ledger vs vendor
  reconciliation mismatches (ADR-0059 section 6), abuse signals, image-version distribution, auth anomalies.
  Alerts to an on-call; paging policy open.
- **Secrets handling (operator side):** KMS-held KEKs, no secrets in images or repos, short-lived node credentials,
  rotation procedure, separation between the secret broker and the orchestrator, provider API keys of the operator never
  enter guests (10), CI secrets scoped and secret-scanned, break-glass credentials only JIT.
- **Supply chain:** pinned, hash-checked Python deps (`uv.lock`), reproducible image build in CI, SBOM, vulnerability
  scan gate, signed images (verify signature at node before boot), pinned Chromium version with fast-patch path,
  dependency review for the marketplace (off by default), minimal base. The project's own `scripts/ci/*` gates exist
  but run on desktop packaging; a server-image gate is **to write** (NOT RUN).

### 11.5 Incident response

Roles (commander, comms, forensics), severity levels, runbooks: (a) suspected environment compromise - network cut,
sleep VM, snapshot for forensics (customer-scope encrypted), rotate secrets, notify; (b) suspected control-plane
compromise - revoke all sessions, rotate KEKs/signing keys, freeze wake-ups; (c) cross-tenant exposure - identify
scope by customer_id, notify customers and the authority within 72 h where personal data is involved; (d) vendor
breach; (e) abusive tenant. Post-incident review, customer notice template. Status: **open** (to write; nothing
rehearsed).

## 12. STOP and kill-switch per customer, remotely

STOP of ADR-0058 is per-product-process and already two-phase (close the door, then independent shutdowns). In the
cloud the unit is the customer environment, with **layers that do not trust each other**:

1. **In-app STOP** (customer's button / API `POST /api/stop`, bound to the session): closes the door in the runtime
   (generation increment, deny approvals, cancel runs). Fast, cooperative.
2. **Gateway STOP** (control plane, authenticated customer or staff-with-reason): sends STOP over the guest-agent
   channel; if the runtime does not confirm within N seconds, escalate. Idempotent; revokes nothing by itself.
3. **Hard stop (orchestrator):** cut egress at the node firewall for that guest (immediate, works even if the guest is
   hostile), then freeze or power off the VM, then snapshot (encrypted) if investigation is needed. This does not rely
   on any guest cooperation.
4. **Account kill:** revoke all sessions + steps 2-3 + block wake.
Rules: the customer can always trigger 1-3 from any signed-in device (and, optionally, via a pre-registered "panic"
channel such as a signed email/SMS link); STOP never deletes data; resume is an explicit action that reopens the
door; staff-initiated hard stops are audited and customer-visible. Late replies after STOP are dropped via the
existing generation mechanism inside the runtime, and by the egress cut outside it. Open point inherited from
ADR-0058: stale tools already invoked and voice stop gaps; a remote STOP cannot recall an email already sent or a
purchase already made by a tool.

## 13. State summary

| Item | State |
|---|---|
| Dependency audit | done (static); nothing run |
| Architecture comparison and recommendation | analysis written; **decision open (Dax)** |
| Node image spec | designed |
| Per-customer volume layout | designed (reuses ADR-0054 root) |
| Identity binding, sessions, recovery | designed; recovery key mode **open** |
| Operator break-glass + customer-visible audit | designed; tooling to build |
| Encryption (envelope, crypto-erase) | designed; confidential computing **open** |
| Export / deletion / GDPR / DPA | designed; legal review **open** |
| Egress control, metadata block, lateral isolation | designed; tier policy **open** |
| Key-in-guest vs key-in-proxy | **open** (recommended: proxy) |
| Abuse handling | designed; ToS/AUP to write; thresholds unmeasured |
| Backup/restore | partial in code (desktop), server design only |
| Hibernate / scale-to-zero | designed; technology depends on provider |
| Incident response | open (to write) |
| Remote STOP layers | designed |
| Controls prepared NOT RUN | none executed; list in section 14 |

## 14. Controls to write and run later (all NOT RUN, none authorized)

Tenant canary search across surfaces; cross-mount refusal (wrong marker/customer); cross-restore refusal; metadata/
private-range/lateral egress probes from inside a guest; allow-list proxy behaviour; resource-limit enforcement
(fork bomb, memory bomb, disk fill) with the neighbours unaffected; wake/sleep correctness and clone-safety of
snapshots; backup -> quarantine restore -> integrity; deletion completeness (volume, snapshots, indexes, control rows);
revocation vs running agent; remote STOP with a hostile (non-cooperating) guest; break-glass audit completeness and
customer visibility; log-schema content-free check; image signature verification; supply-chain gate.

## 15. Decisions for Dax

1. Architecture: A (per-customer microVM/VM + volume + scale-to-zero) for tool-running environments, as the analysis
   recommends, or something else; and whether a shared stateless thin path for chat-only turns is wanted.
2. Egress tier: open web via proxy with deny-lists, or allow-list only (or customer-selectable plans).
3. Engine keys: in the guest (simpler) or only in a node proxy/broker (stronger, gives enforced ledger).
4. Encryption/recovery mode: operator-wrapped (recoverable) vs customer-held (unrecoverable) vs tiers.
5. Marketplace/plugins, vendor-CLI subscriptions, `attach`-to-own-Chrome: confirm they stay off in the cloud.
6. First-login of sites into the persistent browser profile: live view through the gateway vs cookie import.
7. Hibernation default: stop+warm window vs memory snapshots (depends on host capabilities).
8. Whether to pursue confidential computing as a requirement.
9. Legal: controller/processor positions, DPA, ToS/AUP, sub-processor list, DPIA.
10. Provider shortlist is by the other agents; this ADR's Appendix A is the matching checklist.

## Sources (public documentation, NOT fetched in this session; to re-verify before relying on them)

Statements about hypervisor isolation, microVM snapshotting and Chromium sandboxing are general technical knowledge
and are not cited as proof. References to re-check when matching providers:
- Firecracker design and snapshotting: https://github.com/firecracker-microvm/firecracker (docs/snapshotting)
- gVisor: https://gvisor.dev/docs/ ; Kata Containers: https://katacontainers.io/docs/
- Chromium sandbox/user namespaces on Linux: https://chromium.googlesource.com/chromium/src/+/main/docs/linux/suid_sandbox.md
- Cloud metadata SSRF class: https://owasp.org/www-community/attacks/Server_Side_Request_Forgery
- GDPR Art. 28 (processor), Art. 32, Art. 33: https://eur-lex.europa.eu/eli/reg/2016/679/oj

---

## Appendix A - Requirements a host must meet (provider-neutral)

Per architecture; "must" = blocker for that architecture. No provider is evaluated here.

| Requirement | A. microVM/VM per customer | B. hardened containers (gVisor/Kata inside VMs) | C. dedicated VM per customer, always-on (fallback, costly) |
|---|---|---|---|
| Hardware virtualization (KVM or equivalent) exposed to our workload | **must** (bare metal, or nested virtualization enabled on VMs; verify performance and that the provider's AUP allows it) | should (Kata) / not needed (gVisor) | not needed |
| Bare-metal or dedicated-host option | strongly preferred (avoids nested virt) | optional | optional |
| Snapshot / restore of a VM or volume via API (point-in-time, programmatic, fast) | **must** (volume snapshots); memory snapshots nice-to-have | must (volume) | must (volume) |
| Scale-to-zero friendliness: start/stop API, no minimum-uptime billing, per-second or per-minute billing, cheap stopped-instance storage | **must** | must | n/a (cost) |
| Persistent volumes: attach/detach to one instance at a time, encryption at rest with customer-segregated keys or at least our own LUKS layer permitted | **must** | must | must |
| Per-VM firewall / security groups, applied outside the guest, programmable by API, with per-instance egress rules and flow logs | **must** | must | must |
| Egress control: ability to force traffic through our proxy/NAT, block link-local/metadata, fixed egress IPs, SMTP port policy | **must** | must | must |
| Metadata service can be disabled or firewalled from guests | **must** | must | must |
| Private networking / no L2 visibility between tenants' guests | **must** | must | must |
| API for full automation (create, boot, snapshot, destroy, tag) and quotas high enough for our density | **must** | must | must |
| Custom images / bring-your-own kernel and rootfs | **must** (microVM) | should | should |
| Console/serial and out-of-band access for operators (for break-glass) with audit | should | should | should |
| EU region(s); data location contractually fixed; no extra-EU replication by default | **must** | must | must |
| Data Processing Agreement and sub-processor list; stated legal-access exposure | **must** | must | must |
| AUP permits: running a browser with automation, running customer-driven arbitrary code, outbound web crawling at moderate rate; states abuse-complaint handling | **must** (verify in writing) | must | must |
| Abuse/port-25 policy compatible with our blocking | should | should | should |
| Confidential-computing instance types (SEV-SNP/TDX-class) | optional (open decision 8) | optional | optional |
| Time to provision/resume; networking throughput; local NVMe for snapshot restore speed | measure (not asserted) | measure | measure |
| Logging/audit of provider-staff access to our hosts | should | should | should |
| Billing caps or alerts that are actually enforceable (ADR-0059 section 7) | should | should | should |
| Certifications held (ISO 27001, SOC 2) with scope covering the product used | should | should | should |
| Backup storage in a different failure domain, object lock/immutability | should | should | should |
| Termination/exit: export of images and snapshots, no lock-in format | should | should | should |
| Support SLA and security contact | should | should | should |

Anything not documented by the provider in writing is recorded as "unknown", not assumed.

See also ADR-0067 (account -> ONE VM mapping; security and operations on the one-VPS-per-customer baseline): it supersedes the shared-node, scale-to-zero and open-decision statements of this ADR where listed in its section 1.

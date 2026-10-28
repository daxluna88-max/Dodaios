# ADR-0067 - Jarvis Cloud: account -> ONE VM mapping (block 19) and security and operations on the one-VPS-per-customer baseline (block 21)

**Status:** Proposed (design only) · **Date:** 2026-10-28 · **Related:** ADR-0054, 0058, 0059, 0060, 0061, 0062, 0064,
**0065** (this ADR updates it, section 1), **0066** (budget, live view), `docs/headless-vps-deployment.md`.
The environment definition (images, units, firewall files) lives under `deploy/jarvis-cloud-node/` and is written by
another agent. This ADR does not duplicate it; it states the contracts that definition must satisfy.

**NOTHING WAS RUN.** No test, build, service, VM, container, provisioning, provider contact, purchase, account,
download, push or PR. Only reading of the repository and writing of this text. No code was changed. No public
documentation was fetched; section 25 lists references to re-verify, and every technical statement about a product
behaviour (Chromium sandbox, systemd, SQLite, nftables, hypervisor features, OVHcloud VPS capabilities) is general
knowledge, not measured here. Controls are "prepared, NOT RUN". Status legend: **decided** (part of the owner's
baseline), **designed** (specified here, not built), **open** (owner decision or unknown fact).

## 0. Baseline (decided by the owner, not re-opened here)

1. **ONE OVHcloud VPS-2 per customer**, always-on, for the pilot. No purchase has been made.
2. A **separate guest VM per customer** on that VPS is the execution environment (the "customer VM"). Interpretation
   used below: the VPS is the customer's private *host* ("node"); the guest VM runs the Jarvis runtime, Xvfb,
   Chromium and the shell. **Unverified:** whether the VPS offering permits and performs acceptably with nested
   virtualization (KVM inside the VPS). If it does not, the fallback is that the VPS itself is the guest and the
   "host layer" below collapses into a hardened root-owned part of the same OS, which weakens the broker and egress
   isolation (section 14.1 states the effect). This is the first thing to verify in writing before any purchase.
3. No shared singleton state between customers. The control plane holds no customer content.
4. Authenticated HTTPS live view (noVNC class) with explicit human takeover for first logins and 2FA.
5. Inference keys live **outside the guest**, in a host-side broker on the same VPS.
6. Per-customer STOP with process-tree termination. Off-instance coherent backups. Immutable runtime updates.
   Egress/SSRF control. Non-root containment.

Vocabulary: **control plane** (shared, small, no content: accounts, sessions, plans, routing table, ledger, policy
and image catalog, audit store, KMS), **gateway** (control-plane component that terminates public HTTPS),
**host** (the OVH VPS OS, root-owned by the operator tooling, runs the node agent, broker, egress proxy,
nftables, hypervisor), **guest** (the customer VM), **volume** (the customer's encrypted data disk as seen by the
guest), **node agent** (host daemon with the only channel to the control plane).

---

## 1. Supersedes / updates ADR-0065 (explicit conflict list)

ADR-0065 was written before the baseline. It was provider-neutral and assumed a shared fleet. Where it disagrees with
section 0, **this ADR wins**. Each row names the 0065 statement.

| # | ADR-0065 statement | Now | Reason |
|---|---|---|---|
| S1 | Section 2/3, Appendix A: "node" is a host running **many** guests; recommendation is a per-customer microVM with **scale-to-zero** and snapshot hibernate. | A node (VPS) serves **exactly one** customer. The pilot is **always-on**. Scale-to-zero, memory-snapshot hibernate (11.3), warm window and wake orchestration are **not part of the pilot**. Idle cost is a plan-price matter (ADR-0066 compute class becomes a fixed monthly cost, plus caps on inference). | Baseline 1. |
| S2 | Section 3.2, T2, T12: default-deny between guests on a shared node, volume lease between nodes. | There is no co-tenant on a node. Cross-customer isolation is by **separate VPS** (separate provider-level machine and network identity). Lease/fence remains but is local (one volume, one guest, one host). Guest-to-guest rules are replaced by "guest cannot reach the host management plane, other VPSes of the fleet, or the control plane except via the node agent". | Baseline 1-3. |
| S3 | Section 3.2/D4: a "node-local gateway ... terminates TLS and proxies to the guest agent". | TLS and account sessions terminate at the **central gateway**; the node agent keeps an **outbound** authenticated tunnel to it. The VPS exposes **no inbound customer-facing port** (section 4.3). | Routing by authenticated session (section 4.3). |
| S4 | Section 10 / decision 3: engine keys in guest vs proxy was **open** (recommended proxy). | **Decided:** broker outside the guest (section 3.5). The guest holds only a per-VM broker token that is useless elsewhere. | Baseline 5. |
| S5 | Section 8 / decision 4: volume key hierarchy and recovery mode **open**. | Mode 1 (operator-wrapped, recoverable) is the **pilot default**; the 0065 warning stands (no "only you can read it" promise). Mode 2 (customer-held) stays an owner decision (section 23). Keys are per customer; the key never leaves the VPS unwrapped (section 14). | Pilot needs recovery; unattended jobs need the key present. |
| S6 | Section 12: STOP layers; level 3 "orchestrator ... node firewall for that guest". | Same four levels, but level 3 is executed by the **node agent on the customer's own VPS** (nft cut, VM pause/destroy), with a provider-level fallback (VPS reboot/stop through the provider API or console) if the node agent is unreachable (section 17). The cgroup-kill procedure and observed-absence verification are new and bind to the StopController outcomes. | Baseline 6, task. |
| S7 | Section 11.2: backup "off-volume, encrypted per customer, different failure domain". | Tightened: **off-instance** (not on the VPS or its provider backups alone), **write-only from the guest/host**, public-key encrypted before leaving the VM, quiesced (section 18). Any provider snapshot of the VPS is a convenience, **not** the backup. | Baseline 6. |
| S8 | Section 3.3: lazy image roll "at next wake"; Section 11.3 clone-safety of memory snapshots. | Always-on: updates happen in a **maintenance window with restart**, staged by rings (section 16). Memory snapshots are not used, so the clone-safety rule is moot for the pilot. | S1. |
| S9 | Section 3.2: live-view streamer "exposed only via the gateway after auth" (default off in ADR-0066 22.11). | Unchanged in principle; the takeover flow (section 6) makes the **start/stop, limit and logging** precise. ADR-0066 22.11 "read-only first" is kept: takeover is the explicit, separate interactive mode. | Baseline 4. |
| S10 | Section 7 "audit entry shown to the customer" and 2-person approval. | Kept. For a one-person operator team the two-person rule cannot be met; the pilot substitute is **customer consent in-app** plus a delayed, immutable audit entry (section 8). Flagged as a residual risk. | Realism. |
| S11 | Section 5: "Rotate the node-internal Control Key per boot". | Kept, and made part of the **credential bundle** (section 3.5), generation-numbered. | Consistency. |
| S12 | Section 10: egress tier (a)/(b) open. | Default **(a) open web via a host-side proxy with deny-lists and rate limits**; tier (b) allow-list is a plan option. The tier choice remains the owner's (section 14). | Needs PC-like browsing. |
| S13 | Appendix A column A/B/C. | Column **C** (dedicated VM per customer, always-on) plus a guest on top is the baseline; columns A/B are not used for the pilot. | Baseline 1-2. |

Everything else in 0065 (dependency audit D1-D20, volume layout 3.5, threat model T1-T16, GDPR roles, deletion
matrix idea) stays valid unless a row above says otherwise.

---

# BLOCK 19 - Account -> ONE VM -> volume / credentials / session mapping

## 2. Entities

| Entity | What it is | Owner / store | Holds content? |
|---|---|---|---|
| **account** | A person who signs in: email, auth factors, consent records, plan, status. | control plane DB | no |
| **customer** | The tenant that the account owns; the unit of isolation, billing and deletion. In the pilot 1 account : 1 customer. A later "household/team" model adds *members* to a customer; the VM mapping does not change (they share the customer's VM). | control plane DB | no |
| **VM** (as a mapped resource) | The pair {VPS (provider resource) + guest VM}. Mapped by a `vm_id`. | control plane DB (row) + provider inventory | no |
| **volume** | The customer's encrypted data disk, mounted by exactly one guest. Layout of 0065 section 3.5. | the VPS block storage / disk, LUKS-encrypted | yes |
| **credential bundle** | The set of secrets and keys belonging to one VM: tunnel identity, broker token, control key, backup write credential, volume key wrap. Versioned by `bundle_gen`. | split by design (section 3.5) | no content, but secrets |
| **session** | An authenticated login of a device to the account (access + refresh token record). | control plane DB | no |
| **device** | A registered client (browser/phone) with a revocable record. | control plane DB | no |
| **job** | A unit of agent work inside the guest. The control plane stores only a job **index**: id, state, timestamps (0065 section 4). | guest (content), control plane (index) | content only in guest |
| **backup set** | One encrypted, manifest-described snapshot of the volume scope, off-instance. | off-instance object store | yes (encrypted) |
| **takeover session** | One explicit live-view interaction window (section 6). | control plane (metadata), node agent (stream) | no (stream is not stored) |
| **break-glass grant** | A time-boxed operator access case (section 8). | audit store | no |

## 3. Identifiers and invariants

### 3.1 Identifiers

All identifiers are opaque random 128-bit values with a type prefix, never derived from the email, never reused,
never sequential: `acc_`, `cus_`, `vm_`, `vol_`, `bnd_` (bundle, with `bundle_gen`), `ses_`, `dev_`, `job_`, `bks_`,
`tko_` (takeover), `bg_`. The provider's resource id (VPS name/id) is stored as an attribute of `vm_`, never exposed
to customers or logs visible to them. Hostnames (`<anything>.example`) are **cosmetic** and carry no authority.

### 3.2 Invariants (each is a rule the code must enforce and a control to test, NOT RUN)

| ID | Invariant | Enforced by |
|---|---|---|
| I1 | 1 account (owner) : 1 customer : 1 VM : 1 data volume. A `cus_` row has at most one non-terminal `vm_` row (unique partial index). | control-plane DB constraint; orchestrator refuses a second provisioning |
| I2 | A `vol_` is bound to one `cus_` and one `vm_` and carries an on-volume marker `{customer_id, vm_id, vol_id, product}` (extends the ADR-0054 `.product` marker). Mount refuses on any mismatch. | guest agent at mount; node agent before attach |
| I3 | A request reaches a VM only if the **authenticated session** resolves to `cus_` and the routing table maps `cus_` -> `vm_` -> the tunnel whose client certificate subject is that `vm_`. Hostname, path, header or token content never selects the target. | gateway (section 4.3) |
| I4 | A VM's identity (tunnel certificate, broker token, backup credential) is valid only for that `vm_`. No credential is accepted for two `vm_` values; no secret value is common to two VMs. | issuance + verification tables |
| I5 | The control plane never stores customer content (memory, files, prompts, page text, third-party addresses). Metadata only, allow-listed fields. | log/DB schema allow-list (section 19.1) |
| I6 | A backup set carries `customer_id`, `vm_id`, `vol_id` in an authenticated manifest; restore refuses a mismatch (ADR-0064 H6 lesson) and restores into quarantine first. | restore tool |
| I7 | Provider credentials (API tokens for the VPS/object-store provider) exist only in the control plane's orchestrator role. They are never on a VPS, in a guest, in an image, or in logs. | secret-store scoping; secret scan |
| I8 | At most one writer to a volume: the guest mounts it exclusively; the host does not mount it while the guest runs; the product launch lock (`core/product_profile.py`) remains inside. | host + guest |
| I9 | State changes of the mapping (create, rotate, freeze, delete) are idempotent, carry an `operation_id`, and write an audit row before the side effect. | orchestrator |
| I10 | A suspended or frozen account never loses data by that act; deletion is a separate explicit state with a stated delay (section 11). | state machine |

### 3.3 Control-plane tables (shape only, tenant-keyed)

`account(acc, email_hash, factors, status, created)`, `customer(cus, acc_owner, plan, status)`,
`vm(vm, cus, provider_ref, region, state, image_digest, bundle_gen, volume vol)`, `volume(vol, cus, vm, key_wrap_ref,
state)`, `device(dev, acc, label, created, revoked_at)`, `session(ses, acc, dev, refresh_hash, expires, revoked_at)`,
`consent(acc, document_id, version, accepted_at, evidence_hash)`, `job_index(job, cus, state, started, ended)`,
`backup_set(bks, cus, vm, vol, created, size, manifest_hash, verified_at, expires_at)`,
`audit(id, ts, actor_kind, actor, cus, action, scope, reason, result)` in an **append-only** store outside operator
write access (section 8). Row-level security by `cus` on every table that has it.

### 3.4 What the guest can learn about its own mapping

The guest knows its `customer_id`, `vm_id`, `bundle_gen` (from the marker and the enrollment record). It does not
know the account email, other customers' existence, the provider reference, or the routing table.

### 3.5 Credential bundle (per VM, generation-numbered)

| Item | Where it lives | Who can read it | Rotation |
|---|---|---|---|
| Tunnel identity (client key + certificate for `vm_`) | host (node agent), file root-only, never mounted into the guest | node agent | each `bundle_gen`; short-lived cert (days) renewed by the node agent |
| Broker token (guest -> host broker) | issued to the guest at boot, in guest memory/tmpfs file 0400 of the runtime user | runtime user in guest (it is the agent's own inference ticket; it is not an engine key) | each boot and each `bundle_gen`; revocable at the broker instantly |
| Engine API keys (BYO) | host-side broker secret store, encrypted at rest under a host key wrapped by KMS | broker process only | customer-driven; write-only in UI |
| Operator-metered engine keys | **control plane only**, in the central inference proxy; not on any VPS (I4, I7) | central proxy | operator schedule |
| Control Key (runtime <-> gateway internal bearer, D3 of 0065) | injected by node agent at boot | runtime | each boot |
| Volume key (DEK) | wrapped under a KMS KEK; unwrapped into host memory for unlock; handed to the guest only as an opened block device, not as a key | node agent (RAM), KMS | on re-key operation; DEK never rotates in place without re-encrypt (open: cost) |
| Backup write credential | host; scoped to this customer's prefix; **write-only, no delete, no list of other prefixes** | node agent | each `bundle_gen` |
| Backup public recipient key | host and guest may know it | public | per customer, on re-key |
| Backup private key | **not on the VPS**; KMS-wrapped in the control plane (mode 1); used only by the restore tool in quarantine | restore role | per customer; destroying it is the crypto-erase of backups |
| One-time enrollment token | control plane -> provisioning only | 15 min, single use | n/a |

`bundle_gen` rotation (scheduled, e.g. every 30 days, and on any suspicion): the control plane issues the next
bundle, the node agent applies it, the old bundle is revoked at the broker/backup store/tunnel CA after the new one
is confirmed in use; failure of any step leaves both valid for a bounded overlap (e.g. 15 min), then the rotation is
marked `partial` and alerts (failure table, section 13).

## 4. Routing and request path

### 4.1 Components on the path

Browser/phone -> HTTPS -> **gateway** (control plane) -> **tunnel** (initiated outbound by the node agent on the
customer's VPS, mutual TLS, identity = `vm_`) -> node agent -> guest runtime (over a host-only private channel,
vsock or a private tap). Nothing in this path is addressed by hostname after the gateway.

### 4.2 Why a tunnel and not an inbound port

The VPS has a public IP anyway. If the gateway connected inbound, the provider IP + a port become a second route to
the customer environment that bypasses account checks and can be probed. With an **outbound-only tunnel** the VPS can
run with **all inbound ports closed** at the host firewall (and at the provider firewall if available), and the
tunnel certificate subject is the only routing key (I3, I4).

### 4.3 Gateway rules

1. Authenticate the session (access token, device not revoked, account `active`), derive `cus_` from the **server-side
   session record**, never from a request field.
2. Look up `cus_` -> `vm_` -> tunnel. If the tunnel's authenticated subject is not that `vm_`, drop.
3. Strip and rewrite `Host`, `Origin`, `Cookie` and authorization headers; inject the per-boot Control Key at the
   node agent; the guest runtime keeps `surface_security` Host/Origin/CSRF checks configured for its internal name
   (0065 D4), not disabled.
4. Per-session and per-customer rate limits at the gateway (requests/min, concurrent streams, upload size).
5. WebSocket (events, live view) upgrades re-check the session at upgrade and then at a short interval and on every
   mutation (ADR-0062 re-check logic). A revoked session's sockets are closed within seconds.
6. A customer subdomain (if any) is only a convenience for cookies; a request whose subdomain and session disagree is
   rejected; a request with a valid session for A addressed to B's hostname still goes to A's VM or is refused, never to B.
7. Errors are uniform and reveal nothing about other accounts or VM states beyond the caller's own.

## 5. Onboarding and provisioning

### 5.1 Customer-facing flow

1. **Sign-up** with email; no VM yet.
2. **Consent records** (terms, AUP, privacy notice, the statement of what the operator can and cannot see, the
   "PC-like agent acts with your logins" notice, engine-vendor data flow per ADR-0050). Stored as `consent` rows with
   document id, version, time, and a hash of the exact text shown. Re-consent on a version change. Wording needs
   legal review (open).
3. **Email verification** (single-use link, short expiry, rate-limited, uniform responses). Without it nothing is
   provisioned.
4. **Auth factors**: passkey and/or TOTP; one-time recovery codes shown once (section 9).
5. **Plan** selection and payment confirmation (the cost cap of ADR-0066 is set here; no auto top-up).
6. **Provisioning** starts (5.2). The customer sees a progress state, not provider details.
7. First-run guided steps (ADR-0057), BYO engine key entry (write-only), and first logins to third-party sites via
   takeover (section 6).

### 5.2 Provisioning workflow (abstract; provider actions are only create / destroy / reinstall / snapshot)

Driven by the orchestrator role of the control plane. Each step is idempotent under a single `provisioning_id`
(also the provider-side idempotency tag, so a retry finds the resource instead of ordering a second one) and has a
named rollback. State is persisted before each side effect.

| # | State entered | Action | Success evidence | Rollback / compensation if a later step fails |
|---|---|---|---|---|
| 0 | `REQUESTED` | row `vm` created in `planned`; precondition: email verified, consent present, plan paid or free-pilot flag set by owner | rows exist | delete rows |
| 1 | `PROVIDER_CREATING` | provider **create** with tag `provisioning_id` | provider reference stored | provider **destroy** (idempotent, by tag); if destroy fails -> `ORPHAN_SUSPECT` reaper |
| 2 | `PROVIDER_READY` | wait for reachable base state | provider reports ready | as step 1 |
| 3 | `BASE_INSTALLING` | provider **reinstall** with the signed base image reference (or bootstrap from clean OS) | node agent reports its image digest equals the signed catalog digest | reinstall again, else destroy |
| 4 | `ENROLLING` | node agent starts with a one-time enrollment token (delivered by the install path, not via customer-visible channels); control plane issues the tunnel certificate and `bundle_gen` 1 | tunnel up, subject = `vm_` | revoke certificate, token; go back to 3 or destroy |
| 5 | `HOST_HARDENING` | host firewall, egress proxy, broker, no inbound ports, no SSH, kernel params applied from the signed host profile | node agent posts a signed hardening report (hash of applied rules) | back to 3 |
| 6 | `VOLUME_CREATING` | LUKS volume created, DEK generated on the host, wrapped by KMS; marker written | marker verifies; wrapped DEK stored | destroy volume key, wipe, back to 5 |
| 7 | `GUEST_BOOTING` | guest booted from the signed guest image on the volume | guest health OK, `/api/health` over the private channel, image digest matches | stop guest, keep volume, retry |
| 8 | `BACKUP_PRIMED` | backup credential issued; first (empty) backup set written and **verified readable** by the restore role | `backup_set` row verified | disable credential; not fatal (flag `no_backup`, account limited, see failure table) |
| 9 | `ACTIVE` | routing row switched to active; customer notified | gateway health check through the tunnel | n/a |

Rules: (a) **Order resources only after payment/plan confirmation**, never on sign-up alone, and never more than the
operator-level global cap of VPS (a breaker like ADR-0066 20.10). (b) A reaper compares provider inventory (by tag)
with the DB hourly: a provider resource without a live DB row is `ORPHAN_SUSPECT`, alerted, destroyed only after an
operator confirmation in the pilot (cost and data loss are both asymmetric). (c) Provisioning never copies another
customer's image state: guest images are built from the catalog, never cloned from a customer VM. (d) Partial
failure leaves the customer in `PROVISIONING_FAILED` with a retry button; no half-active routing.

## 6. Human login takeover (live view)

Purpose: the customer logs in to third-party sites (including 2FA) in the persistent browser profile, themselves,
because the agent must not receive or type their passwords or codes. This is **a person using the guest's browser**,
not the agent.

**Start (explicit):** the customer taps "Take over" in the app, after step-up re-authentication (passkey/TOTP) if the
last strong auth is older than a few minutes. The control plane creates a `tko_` record `{cus, ses, dev, started,
max_duration, mode}` and a **one-time stream ticket** (single use, 60 s, bound to the session and device, not in a
URL query that gets logged; carried in a header or first WebSocket message). Only one takeover per customer at a time.

**Mode:** `read_only` (watch) or `interactive` (takeover). Interactive is a separate explicit choice, with a visible
"Live view attivo - controllo tuo" indicator on every device of the customer, and an **agent pause**: while
interactive, the runtime holds the agent's own browser/GUI input (door closed for the input device) so agent and human
do not type into the same window. This is a STOP-lite for input only; it is not STOP and does not kill jobs.

**Time limit:** default maximum 10 minutes interactive (renewable by an explicit tap, at most N renewals), idle
timeout 2 minutes of no input, hard maximum per day. All limits are plan-configurable and are the owner's values
(open); the numbers here are proposals, not measured.

**Stop (explicit or automatic):** customer taps "End", device/session revoked, timeout, tunnel loss, gateway restart,
STOP at any level, or account suspend. On stop: stream ticket revoked, the stream socket closed on both ends, the
input channel dropped, the agent's input door reopened **only if no STOP is in force** and the customer chooses
"resume agent" (default: the agent stays paused until the customer resumes, so a forgotten takeover never lets the
agent continue unseen into a half-logged-in state).

**What is logged (control plane, metadata only):** `tko_` id, start/stop time, mode, reason of end, duration, device
id, number of renewals, whether step-up succeeded, bytes transferred (for the egress/budget class, ADR-0066 20.2).
Customer-visible in their activity list.

**What is never logged or stored:** keystrokes, clipboard, pixels, screenshots, recordings, window titles, URLs
with query, form values, the typed characters, TOTP codes, or the contents of password fields. The stream is a live
pipe, not stored by operator components. Operator staff cannot join a customer's takeover. No recording without a
separate consent (ADR-0066 22.11 rule stands). The stream is excluded from any content log even at debug level;
a debug build that logs stream data must not be deployable (log schema allow-list, section 19.1).

**Credential-field protection (best effort, designed):** while takeover is interactive the runtime does not take
screenshots for the model and does not read the DOM; after the takeover ends the agent sees the resulting logged-in
state only. Cookies/session storage end up in the browser profile on the encrypted volume and in backups (section 18),
which must be said to the customer.

**Threats specific to this flow:** hijack of the stream (one-time ticket, TLS, session binding, no direct port: 0065
T15), clickjacking of the "Take over" button (frame-ancestors none, step-up), a hostile page the customer opens during
takeover (it runs in the guest, not in the customer's device: the only data crossing is pixels and input), leaving a
logged-in session behind for the agent to use (that is the purpose, so the customer must understand that Jarvis then
acts as them on that site).

## 7. Revocation, suspension, freeze, STOP (they are different things)

| Action | Who | Effect on access | Effect on a running job | Effect on data/VM | Reversible |
|---|---|---|---|---|---|
| **Revoke device / session** | customer (any signed-in device), account owner | the session cannot send commands; sockets close | **none**: an already started job continues; results are queued | none | re-login |
| **Revoke all sessions and stop everything** | customer | all sessions ended; then STOP level 1-3 (section 17) | stopped, with the StopController outcomes | none | explicit resume |
| **STOP** (levels 1-4 of 0058/0065) | customer; or operator with audit | per level | per level; outcomes `requested/confirmed/still_active/unknown` | never deletes | resume is explicit |
| **Account suspend** | operator (abuse/payment) or customer-initiated | login refused, new jobs refused; export remains for a stated period | STOP level 2 then 3 is part of suspend for abuse; for payment suspend, a running job may finish within a stated grace unless the budget breaker forces stop | VM kept, egress throttled or cut, volume kept | yes |
| **VM freeze** | operator/incident | VM paused or powered off at host level, egress cut; tunnel stays so a status page can show "frozen" | killed (no confirmation expected from the guest) | volume kept; encrypted snapshot optional for forensics | yes, by operator, customer notified |
| **Decommission** | customer request or terminal suspend | account closed | stopped | wipe path (section 11) | no |

Rules: (1) Revocation never cancels a started job unless the same click includes STOP; the UI says so in plain words
at the revoke button (consistent with 0059 section 4 and 0062). (2) Mutations are re-checked against the session
store each time (no cached allow). (3) A revoked device's refresh token is burnt; a stolen refresh token reused after
rotation revokes the whole token family and alerts. (4) A device revoke does not rotate the **bundle**; a suspected
compromise of the account does (rotate `bundle_gen`, see section 3.5). (5) Operator actions on 'suspend/freeze' are
audited and customer-visible (section 8) unless law or safety forbids; the exception is itself logged internally.

## 8. Operator break-glass with customer-visible audit

Builds on 0065 section 7; adds the mapping-specific mechanics.

- **Default:** no operator interactive access to any guest. The VPS has no SSH daemon enabled. The provider's
  out-of-band console (if the provider has one; unverified) is a break-glass path whose use must be logged by the
  operator procedure, because the provider may not log it for us.
- **Grant:** a `bg_` record `{cus, scope, reason, requester, approver, start, expires, customer_consent_ref}`. Scopes:
  `metadata` (default, always on), `logs` (operator logs of that VM, still content-free), `status_shell` (host-only
  commands on the node agent, no guest filesystem), `screen_view` (needs customer consent), `volume_read` (rare,
  incident only, needs consent or defined legal/safety exception).
- **Mechanism:** just-in-time credentials with a TTL of at most 60 minutes, minted by the control plane and accepted
  by the node agent; no standing keys. The node agent writes the audit line locally and forwards it; the audit store
  is append-only and operator-write-only through a path that cannot delete.
- **Customer-visible record:** within minutes (or at the end of the session where live disclosure would harm an
  investigation, with a maximum delay stated in the terms), the customer's activity page shows: time, scope, reason
  class, duration, whether data was viewed, who (role, not name). Failure to write the audit entry **denies** the
  access (fail closed).
- **Secrets stay out:** the host-side broker secret store and the volume `.secrets/` are excluded from every scope
  except a signed, explicit `volume_read` with a stated reason; even then reveal of engine keys is not offered.
- **Pilot reality:** with one operator, "two-person approval" cannot be enforced. The pilot control is the customer's
  explicit in-app consent for non-emergency access, and the delayed immutable audit. This is a residual risk (section 22),
  and the claim "operators cannot read your data" must not appear in product text (mode 1 keys, host memory).
- Status: **designed**; audit store, JIT mechanism and customer page to build.

## 9. Account recovery without weakening isolation

Recovery restores access to the **same** mapped VM; it never repoints a VM to another account, never moves a volume
between customers, never gives support a path in.

1. Factors: passkeys (several), recovery codes (hashed server-side, shown once), optional second channel. Email alone
   is the weakest root of trust: recovery by email alone triggers a **waiting period (proposed 72 h)** with
   notifications to all devices and the cancel link; during it the account is still usable by existing sessions,
   and **step-up-protected actions (export, delete, key changes, broker key entry, takeover) are blocked** until the
   wait ends.
2. Successful recovery: all sessions revoked, all devices unlisted, `bundle_gen` rotated, broker token revoked, the
   customer re-adds devices, and (cheap protection against an attacker who held the account) engine keys in the broker
   are **marked "re-confirm"**: the broker refuses to use BYO keys until the customer confirms (the attacker may have
   added their own).
3. Uniform, rate-limited responses; no account enumeration; no security questions; support cannot "reset to a new
   email". A support request to change the account owner requires the same factors and the same wait.
4. If all factors are lost: the customer can use the waiting-period path; if even the email is lost, the answer is
   "new account, old data is deleted after the stated period" or an identity-document process that is **not offered**
   in the pilot (open: it is a social-engineering path).
5. Mode 2 customers (customer-held key) cannot be recovered by design; stated at sign-up (open choice, 11.4).
6. The mapping check I1 is re-run on recovery: still one VM, same `vm_`, same `vol_` marker.

## 10. What the agent can and cannot reach (from inside the guest)

| Target | Reachable? | How / why |
|---|---|---|
| Its own volume (`/data`, `home/`, `work/`) | yes | mounted |
| Inference engines | **only through the broker** on the host private channel, with a per-VM token; the broker holds the keys, enforces the budget reserve (ADR-0066), per-call and per-minute limits, and the engine allow-list | guest has no route to engine hosts directly (egress proxy denies them for the guest, or allows only via broker) |
| Engine API keys (BYO or operator) | **never** in the guest, not in env, not in files, not in memory of any guest process | I4, I7 |
| Provider (OVH) API or console tokens | **no** | exist only in control-plane orchestrator role |
| Host management plane, node agent API beyond its narrow channel | no (only: health, secret receive, STOP hook, live view relay) | host-only bridge with default-drop; node agent authenticates the guest by channel |
| Control plane | no direct route; the guest speaks only to the node agent | egress deny |
| Other customers' VPSes, fleet IPs | no | deny-list pushed by the control plane, plus no inbound ports on those VPSes anyway |
| Cloud/provider metadata and link-local | no | section 11 |
| The public internet | yes via the host egress proxy under tier (a) or the allow-list under tier (b) | section 14 |
| SMTP out | no | blocked, section 14.4 |
| Backup store | **no** | the host's backup agent uploads; the guest cannot reach it or read other sets; guest can request "backup now" through the node agent |
| Update source | no | in-guest updater disabled (0065 D14) |
| Its own cgroup/limits, capabilities, host kernel | no changes possible | non-root, sudo-less (section 15) |

The agent therefore holds: the customer's own files, the browser profile cookies (the PC's logins), a broker token,
and a Control Key. A hostile agent can misuse the logins in the profile (the product purpose) and can spend broker
budget up to the caps; it cannot obtain provider access, engine keys, other customers, or backup history.

## 11. (Block 19 closing) Decommission and data deletion

Trigger: customer deletes the account (step-up, typed confirmation, delay), terminal suspension, or non-payment
past the retention period.

| Step | Action | Evidence recorded |
|---|---|---|
| D1 | State `DELETING`: sessions revoked, STOP levels 1-3 with outcomes recorded, wake/start blocked, billing stopped | audit rows |
| D2 | Offer export window (stated length, e.g. 7-30 days after request unless the customer waives; illegal-content suspensions excepted) | export record |
| D3 | **Crypto-erase**: destroy the wrapped DEK of the volume (KMS) and the backup private key wrap | KMS destroy receipt |
| D4 | **Overwrite/wipe**: provider **reinstall** (fresh OS) of the VPS, then **destroy** the VPS. Reinstall is done before destroy because the provider may reuse disk space; the claim is "reinstalled then destroyed", not "securely wiped" (no provider guarantee assumed) | provider operation ids |
| D5 | Backups: delete all sets of `cus_` the store permits; objects under object-lock cannot be deleted until the lock expires, so **the lock/retention maximum is the stated deletion horizon** (proposal: at most 35 days; open). Because the private key is destroyed in D3, the remaining sets are unreadable even before expiry | per-set expiry list |
| D6 | Control-plane rows: delete content-adjacent rows; keep only legally required billing/abuse records for the stated period; replace identifiers by a tombstone hash | row counts |
| D7 | Operator logs: metadata only, expire under the fixed short retention | policy |
| D8 | Customer receipt: what was deleted, what remains and until when (provider-side backups of the host, engine-vendor retention, logs, legal records). "Archived" is never called "deleted" | receipt |

Deletion completeness is a control to write (NOT RUN, section 21). Third-party effects (sent emails, posted content,
engine-vendor copies) are outside deletion; the receipt says so.

## 13. Failure and partial states (mapping and lifecycle)

| # | Situation | Detected by | State shown | Automatic action | Operator action | Data risk |
|---|---|---|---|---|---|---|
| F1 | Provider create succeeded, DB write failed | reaper: tagged resource without row | `ORPHAN_SUSPECT` (internal) | alert | confirm then destroy, or adopt by `provisioning_id` | cost only |
| F2 | DB row exists, provider create failed/timed out | step 1 timeout | `PROVISIONING_FAILED` | retry with the same `provisioning_id` (finds or creates once) | review after N retries | none |
| F3 | Reinstall done, node agent never enrolls | enrollment deadline | `PROVISIONING_FAILED` | revoke token, retry reinstall once | investigate | none |
| F4 | Enrollment OK, hardening report missing or hash mismatch | step 5 | `NOT_ACTIVE` | never route; retry | investigate | none |
| F5 | Volume marker mismatch at mount | guest/node agent | `MOUNT_REFUSED` | refuse to start guest | investigate (possible wrong volume attach) | none (refused) |
| F6 | Tunnel down, VPS up | gateway heartbeat | customer sees `UNREACHABLE` (not Idle, not Stopped: 0058 rule) | none beyond retries | check provider status; if STOP pending and unreachable, level-3 escalation to provider | **STOP outcome `unknown`** |
| F7 | VPS down/provider outage | provider status + heartbeat | `UNREACHABLE` | none | provider ticket; backup restore plan if loss | availability |
| F8 | Guest crashed, host fine | node agent | `GUEST_DOWN` | restart guest; restart-loop cap | investigate; image rollback | tabs/jobs lost |
| F9 | Runtime hung (health fails) | guest agent | `DEGRADED` | restart runtime after STOP level 1-2; keep volume | investigate | in-flight job state |
| F10 | Disk full on volume | node agent metric | `DEGRADED` | refuse writes above quota (ADR-0066), stop agent writes | extend or customer clean-up | corruption risk if reached |
| F11 | Backup failing N times | backup monitor | `BACKUP_STALE` | keep trying, banner to operator; customer notice after threshold | fix; do not run risky updates | restore point ages |
| F12 | Backup uploaded but verify (read-back/hash) failed | verifier | `BACKUP_UNVERIFIED` | do not count it as a restore point; retry | investigate | false sense of safety |
| F13 | Update failed mid-way | node agent | `UPDATE_FAILED` | automatic rollback to previous slot if health fails twice | investigate | none if pre-update backup verified (else update refused) |
| F14 | Rotation partial (new bundle applied, old not revoked, or reverse) | rotation controller | `ROTATION_PARTIAL` | keep overlap bounded; finish or revert; alert | manual resolve | a stale credential stays valid |
| F15 | Duplicate provisioning request (double click, retry) | idempotency key | n/a | return the existing operation | none | none |
| F16 | Two accounts claim the same VPS ref (bug) | unique constraint | `CONFLICT` | refuse | incident | cross-tenant risk, treated as security incident |
| F17 | Account suspended while a job runs | state machine | `SUSPENDED` | per section 7 | n/a | partial work in volume |
| F18 | Deletion partially done (crypto-erase done, VPS destroy failed) | state machine | `DELETING_PARTIAL` | retry destroy; VPS stays egress-cut | confirm destroy | data unreadable but resource alive |
| F19 | Takeover started, tunnel dropped | gateway | `tko_` ended `link_lost` | agent input stays paused until resumed | none | none |
| F20 | KMS unreachable at boot | node agent | `LOCKED` | do not boot guest; retry | KMS recovery | availability only |
| F21 | Clock skew breaks certs/tickets | NTP monitor | `DEGRADED` | refuse tokens outside skew | fix | availability |
| F22 | STOP requested, node agent unreachable | StopController | `unknown` | escalate to provider-level power action after timeout | confirm | external effects may continue |

Principle for every row: a state that cannot be observed is shown as **unknown** or **unreachable**, never as
idle/stopped/ready (ADR-0058).

---

# BLOCK 21 - Security and operations

## 14. External egress and SSRF control

### 14.1 Who enforces what

| Layer | Component | Enforces | Trust |
|---|---|---|---|
| P. Provider network (if the product offers a VPS firewall; **unverified**) | provider firewall | inbound default-deny; optionally outbound coarse rules | outside our code; defence in depth only |
| H. Host | nftables on the VPS **host**, egress proxy, host resolver, broker | **authoritative** for guest traffic: the guest has no direct route out; all guest egress is forced to the proxy or dropped | root on host only; guest cannot alter |
| G. Guest | nftables in the guest, the Python egress guard (0053/0060) | defence in depth, early error messages | root-in-guest could change it, so never relied on |
| C. Control plane / gateway | policy catalog, per-customer limits, abuse decisions | publishes **signed policy bundles** (deny sets, plan limits, fleet IP list); rate limits on **inbound** and on the **inference path**; decides suspend/throttle; does not sit in the outbound data path | decides, does not carry traffic |

Fallback caveat (section 0.2): if no guest VM can be run on the VPS, layer H and G collapse; the broker, proxy and
nftables are then in the same kernel as the agent and a root compromise of the agent defeats them. That is a much
weaker baseline; do not buy on that assumption without the owner's explicit acceptance.

### 14.2 Rules at the host (layer H)

1. **Inbound:** drop everything except established traffic. No SSH. The node agent dials out only.
2. **Guest -> anywhere:** default drop. Allowed: (a) TCP to the host egress proxy on a private address; (b) TCP to the
   broker; (c) the node agent channel. No direct UDP, no direct DNS to the world, no ICMP out except to the host.
3. **QUIC/UDP 443, WebRTC, STUN:** dropped (browsers fall back to TCP); Chromium policy also disables WebRTC local-IP
   handling, as a convenience, not a control.
4. **Ports from the proxy side:** allow destination ports 80 and 443 (and a short, owner-approved list); deny 25, 465,
   587, 2525 (mail), 22 (rate-limited or denied), 23, 3389, 5900-5999, 6667-6669 (IRC), database ports, and anything
   else.
5. **IPv6:** v1 disables IPv6 in the guest (sysctl) and drops v6 at the host `inet` table with default drop, so there
   is no v6 side channel; if v6 is later enabled, the same deny sets apply to v6 (below) and router advertisements
   from the guest are dropped. Reasoning: the dual-stack deny list is the common source of bypasses (IPv4-mapped,
   NAT64).
6. **Anti-spoofing** on the guest tap (source address and MAC pinned), no bridging with other interfaces.
7. **Per-customer limits:** new connections/second, concurrent connections (conntrack limit), bytes/day and
   bytes/month (feeds ADR-0066 egress class), distinct destination hosts per hour, request rate per destination.
   Exceeding -> throttle first, then the abuse ladder (section 20.2).

### 14.3 Egress proxy behaviour (SSRF)

The proxy runs on the host, is not reachable from outside, and treats the guest as hostile.

- **It resolves names itself.** The guest has no resolver of its own (or a stub that only the proxy may answer). The
  browser is configured to use the proxy with proxy-side resolution (SOCKS5h or HTTP CONNECT with a hostname), DoH
  disabled by policy, so Chromium's resolver never makes the decision.
- **DNS rebinding:** resolve once per connection, evaluate **every returned address** (A and AAAA) against the deny
  set, **connect to the validated IP literal** (pin), do not re-resolve between check and connect, ignore TTLs below a
  floor, and re-validate on each new connection. A name returning a public and a private address together is refused
  (not "pick the public one").
- **Redirects:** the proxy never follows redirects on behalf of a client. Every hop is a new request through the proxy
  and is re-validated (scheme, host, resolved IP, port). Redirects to `file:`, `gopher:`, `ftp:` and similar are the
  client's problem; the proxy only speaks HTTP(S) CONNECT/forward on allowed ports.
- **Address forms:** reject numeric host forms that are not canonical dotted decimal / bracketed hex (decimal
  integer, octal, hex, short forms like `127.1`), URL userinfo tricks, and trailing-dot and Unicode confusables are
  normalised before the check.
- **Deny set (v4):** 0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, **169.254.0.0/16** (link-local and cloud
  metadata, e.g. 169.254.169.254), 172.16.0.0/12, 192.0.0.0/24, 192.0.2.0/24, 192.168.0.0/16, 198.18.0.0/15,
  198.51.100.0/24, 203.0.113.0/24, 224.0.0.0/4, 240.0.0.0/4, 255.255.255.255/32; **plus** the host's own public
  and private addresses, the guest gateway, the provider's management/metadata ranges (**to list per provider, from
  documentation; unknown now**), and the fleet IP list pushed by the control plane (other customers' VPSes).
- **Deny set (v6, if ever enabled):** ::1, ::/128, fc00::/7 (unique local, incl. `fd00:ec2::254`-class metadata),
  fe80::/10, ff00::/8, ::ffff:0:0/96 (IPv4-mapped: **unwrap and re-check as v4**), 64:ff9b::/96 (NAT64: unwrap),
  2002::/16 (6to4: unwrap), 2001::/32 (Teredo), documentation ranges.
- **Metadata endpoints:** none are intended to be exposed to the guest; the host's own metadata access (if the
  provider has any) is firewalled from the guest and the host never puts provider credentials where the guest path can
  reach them (I7). A probe from inside the guest to the link-local addresses and host IPs is written as a control (NOT RUN).
- **Content-free logging:** destination host (or IP), port, bytes, outcome, duration. No URL path or query, no headers.
- **TLS:** the proxy does not intercept TLS (no MITM CA in the guest); it sees SNI/host only. This limits detection
  (cannot see what is sent) in exchange for not holding a decryption capability over every customer's traffic.

### 14.4 Outbound SMTP and abuse limits

SMTP direct send is dropped at layer H. The agent's email is through the customer's own mail provider logins
(webmail/API over HTTPS) or a future operator mail relay with per-customer quotas and DKIM (not designed here).
HTTP-based sending is limited by the abuse limits: requests/min, distinct hosts/hour, form-submission count
(heuristic, metadata only), captcha-solving frequency, and reports to the abuse contact. Thresholds are
**unmeasured**; defaults are owner decisions.

### 14.5 What the control plane enforces vs what the VM enforces (summary)

Control plane/gateway: inbound auth, session routing, rate limits (inbound, inference), policy signing/distribution,
abuse state, budget ledger. VM host (nftables + proxy + broker): authoritative outbound filtering, DNS, ports, v6,
SMTP, per-customer connection and byte caps. Guest: advisory only.

## 15. Non-root containment

Inside the guest (extends 0065 section 3.1; the environment definition owns the files):

- **Users:** `jarvis` (fixed uid, no login shell for remote access, no password), runtime and tools run as it. A
  separate root-owned guest agent (small, no network listener except the private channel). No other interactive
  users. **No sudo, no su rules, no setuid helpers beyond the distro minimum** (audit and strip). No SSH server in
  the guest.
- **Capabilities:** the runtime service has an empty bounding set (`CapabilityBoundingSet=` empty,
  `AmbientCapabilities=` empty), `NoNewPrivileges=yes`, no `CAP_NET_ADMIN`, no `CAP_SYS_ADMIN`, no `CAP_SYS_PTRACE`
  across users, no raw sockets (`RestrictAddressFamilies=AF_UNIX AF_INET` (AF_INET6 if enabled)).
- **systemd hardening (service level):** `ProtectSystem=strict`, `ReadWritePaths=/data /tmp-nonexec-if-possible`,
  `ProtectHome=` as needed (home is on the volume), `PrivateDevices=yes`, `ProtectKernelTunables/Modules/Logs=yes`,
  `ProtectProc=invisible`, `RestrictNamespaces=` configured per the Chromium note below, `LockPersonality=yes`,
  `MemoryDenyWriteExecute=` **cannot** be enabled for Chromium/V8 (JIT), so it is off for the browser unit and stated
  as residual, `SystemCallFilter` applied to the runtime unit; `TasksMax`, `MemoryMax`, `CPUQuota`, `IOWeight` per unit.
- **seccomp/AppArmor notes:** Chromium uses its own seccomp-BPF in renderer/GPU/utility processes; do **not** wrap it
  in a profile that blocks what its sandbox needs. A restrictive systemd `SystemCallFilter` is applied to the Python
  runtime and the tool runner but not to Chromium's browser process. An AppArmor (or SELinux) profile for the runtime
  confines file access to `/data`, `/opt/jarvis` (read-only), `/tmp`; the profile is a second layer, written from the
  environment definition, and **not tested** here.
- **Chromium sandbox preserved:** run Chromium **without `--no-sandbox`** as the non-root `jarvis`. Chromium's
  sandbox as non-root relies on unprivileged user namespaces (or a setuid helper, discouraged). So the guest kernel
  must permit `user.max_user_namespaces > 0`, and on distros with an LSM that restricts unprivileged user
  namespaces by default (some recent Ubuntu releases do, through AppArmor), a **specific AppArmor profile that
  allows `userns` for the Chromium binary only** is required instead of disabling the restriction globally. This
  reverses the 0065 D9 allowance of `--no-sandbox` "because the VM is the boundary": with a VM per customer the
  sandbox is **still kept** because the browser renders hostile pages and holds the customer's logins; the VM
  protects other customers, the sandbox protects the customer's own files and cookies from a renderer exploit.
  If, after the owner measures it, the sandbox cannot run in the chosen image, `--no-sandbox` returns **only** as a
  recorded exception in the residual-risk table, not silently. (Unverified: exact kernel parameters and profile for
  the chosen image.)
- **Resource isolation:** one systemd slice for the tools (`jarvis-tools.slice`) separate from the runtime slice, so
  STOP can kill the tools without killing the guest agent (section 17); `pids.max`, memory cap with
  `MemoryHigh`, disk quota on `/data`, no swap thrash policy (decision with sizing, open).
- **Filesystem:** read-only root; `/data` mounted `nosuid,nodev`; `noexec` where Chromium/Python allow it (test);
  `/tmp` tmpfs; no mounts of host paths into the guest (no shared filesystem), no 9p/virtiofs.
- **What non-root does not do:** it does not stop the agent from using the customer's logged-in sessions, from
  spending broker budget, or from exfiltrating the customer's files to a permitted destination (T3). Those are
  governed by approvals, egress and budget, not by uid.

## 16. Immutable runtime updates, rollback, staged rollout

- **Unit:** a **signed bundle** = {guest root image or read-only runtime tree, locked Python deps with hashes,
  Chromium build, host profile hash, schema version, version number, release notes class (normal/security),
  min-compatible previous version}. Detached signature by a release key kept offline (not in CI), public key pinned
  in the host image. The **node agent verifies** the signature and a monotonic version (no downgrade except a
  signed rollback record) **before** staging. The guest never fetches updates; the guest has no route to the source.
- **Delivery:** the control plane publishes the catalog (digests, rings); the node agent pulls the bundle itself
  (outbound) from the catalog store, verifies, and stages it in the inactive slot (A/B). Atomic switch = point the
  guest boot to the other slot; rollback = switch back.
- **Immutability in the guest:** read-only root; the install is replaced, not modified. A customer or a tool cannot
  rewrite it (0065 D14).
- **Migrations:** a data-schema change requires a **verified pre-update backup set** (section 18); if no verified
  backup exists the update is refused (the 0064 H5 rule `no_verified_backup`; the override is owner-only and
  logged). A migration that cannot be reverted is flagged and needs the owner.
- **Staged rollout:** ring 0 staff-owned VMs -> ring 1 canary (small percentage or named volunteers; with 1:1 VPS
  per customer the "fleet" is small, so canary may be a single customer who opted in) -> ring 2 -> ring 3 all.
  Promotion gates: no health regression (runtime up, `/api/health`, startup time, error rate, broker error rate),
  backup after update succeeds, a hold time (e.g. 24 h, owner value). Any gate failure **halts the ring** and
  auto-rolls-back the affected VMs. A kill-list of bad digests prevents re-staging.
- **Timing:** always-on means restart; updates run in a per-customer maintenance window (default local night),
  deferrable except `security` class which has a deadline. A running job delays the restart until it finishes or the
  customer approves; a security update can override with notice.
- **Host updates** (OS packages, kernel, hypervisor): the same signed-bundle path for the host profile; unattended
  security updates for the host base only through the signed catalog (no ad-hoc `apt` on customer VPSes), cadence in
  section 20.1.
- Status: designed; build and rollout never run.

## 17. Per-customer STOP and process-tree termination

Reuses the four outcomes of ADR-0058 per subsystem: **requested** (the request was sent), **confirmed** (observed
stopped), **still_active** (did not stop within the deadline), **unknown** (no answer, no telemetry). The strip shows
"Stopped" only when all relevant parts are `confirmed` (0058 rule, unchanged).

| Level | Executor | Action | Confirms by |
|---|---|---|---|
| 1 In-app STOP | runtime (StopController, 0058) | close the door (epoch increment), deny approvals, cancel runs, mission cancel | runtime probes (existing) |
| 2 Guest process-tree kill | guest agent | **kill every process of the tool slice by cgroup**, see below | observed absence (below) |
| 3 Host hard stop | node agent on the VPS | cut the guest's egress at nftables (immediate), then pause/stop the guest VM | host observation: guest state paused/stopped + counters flat |
| 4 Account kill | control plane | revoke sessions, level 1-3, block restart, provider-level power action if node agent is unreachable | all of the above plus provider state |

**Level 2 mechanism (designed; tested only as a control, NOT RUN):**
1. Every agent-started process (shell, browser, tool subprocess, MCP stdio server, GUI apps) is placed in
   `jarvis-tools.slice` (systemd), by running the runtime's child spawning through `systemd-run` scopes or by
   inheriting the slice from the runtime service via `Delegate=yes` sub-groups. Daemonizing, `setsid`, double-fork
   or `nohup` do not leave a cgroup.
2. Kill: write `1` to the slice's `cgroup.kill` (cgroup v2, kernel >= 5.14) or `systemctl kill --kill-whom=all
   --signal=SIGKILL jarvis-tools.slice`; the unit's `KillMode=control-group` makes `systemctl stop` do the same with a
   TERM grace first. The runtime itself is **not** in the tool slice, so it can report.
3. **Verification by observed absence, two independent observations, both required for `confirmed`:** (a) the
   slice's `cgroup.procs` (recursively over child cgroups) is empty and `cgroup.events` shows `populated 0`; (b) a
   process scan finds no process owned by the `jarvis` uid except the runtime's known pids (catches anything that
   escaped the cgroup via a user-level `systemd --user` manager; we therefore also do not start a user manager for
   `jarvis` and set `linger` off). Zombies are not counted as active (0058 rule) but are listed.
4. Outcomes mapped to the StopController: kill sent -> `requested`; both observations clean -> `confirmed`;
   processes remain after the deadline and a second kill -> `still_active` (escalate to level 3); guest agent silent
   or the check cannot run -> `unknown` (escalate to level 3 after a timeout). **"Unknown" is never turned into
   "stopped" because the kill was sent.**
5. The runtime's own `Stopped` for `missions` currently relies on tracked pids and `psutil` (0058); the cgroup check
   **adds** an OS-level source that is independent of the runtime's bookkeeping. The mapping of this source into
   `StopController` as a new probe is **to implement** (not done here).
6. Restart is explicit: the customer resumes; the tool slice is recreated clean; the epoch increments.

**Level 3 specifics:** nft cut first because it works even if the guest is hostile and does not wait for the guest;
then stop the guest. Stopped guest is `confirmed` from the hypervisor state, not from guest cooperation. A snapshot
for forensics is optional and encrypted under the customer key.

**Limits stated to the customer:** STOP cannot recall an email sent, a purchase made, a post published, or a request
already accepted by a third party. Stale tool results are dropped by the epoch mechanism (0058). A kill can corrupt a
half-written file or a SQLite transaction in flight; SQLite recovers, plain files may not (backups exist, section 18).

## 18. Off-instance coherent backup, restore, export, delete

### 18.1 Principles

1. **Off-instance:** the backup lives in a store that is not on the customer VPS, not controlled by the guest, and not
   only a provider snapshot of that same VPS (S7). The store is a separate failure domain, ideally another account
   or project, with object lock/versioning; **provider and product choice are open** (no account is opened here).
2. **Write-only:** the VPS holds a credential that can add objects under `cus_/` but cannot delete, overwrite or list
   other prefixes; so a compromised guest or a ransomware-like agent cannot destroy history. Deletion is by the
   control plane's retention role only.
3. **Encrypted before leaving the VM**, with a per-customer public recipient key (age/X25519-class or equivalent
   hybrid scheme; library choice open). The VPS can encrypt but cannot decrypt old sets. The private key is
   KMS-wrapped in the control plane (mode 1) and used only by the restore role into quarantine.
4. **Coherent, not merely crash-consistent**, with the manifest saying which one a set is.
5. A backup is not a restore point until it has been **verified** (read back, hashed, and ideally restore-drilled).

### 18.2 Procedure (application-consistent)

| Step | Action | Why |
|---|---|---|
| 1 | Scheduled (daily) and event-driven (pre-update, pre-delete, on request) trigger from the node agent | cadence |
| 2 | **Quiesce admission:** the runtime closes the door for *new* jobs and for new writes by long tasks (a soft "backup pause", distinct from STOP: no kill, no cancel). Running jobs are given a short window to reach a checkpoint; if they do not, the set is taken as `consistency: crash` and flagged, never labelled `quiesced` | cross-database coherence |
| 3 | **SQLite:** every `*.sqlite|*.db|*.sqlite3` copied via the SQLite online backup API (or `VACUUM INTO`) from a read-only connection, then `PRAGMA integrity_check` on the copy; `-wal`/`-shm` never copied raw (the ADR-0064 H5 method in `core/product_backup.py`, extended to all customer sub-directories because 0064 admits it covers top-level only) | coherent DB copies |
| 4 | **Cross-database consistency:** all database copies are taken inside the same admission pause, so no write lands between database A and B (0064 H5 residual) | closes the H5 gap |
| 5 | **Browser profile:** Chromium holds locked SQLite/LevelDB files (Cookies, Local Storage, IndexedDB). Procedure: close the browser gracefully (runtime-controlled, tabs are restorable but not preserved), copy the profile **excluding caches** (Cache, Code Cache, GPUCache, Service Worker cache), then restart if the agent needs it. If a long task holds the browser open, the profile is copied best-effort and flagged `browser_profile: inconsistent`; the customer may need to log in again after a restore (stated) | profile consistency |
| 6 | **Files in `home/` and `work/`:** filesystem-level freeze/snapshot (LVM/btrfs/ZFS snapshot or `fsfreeze` window) then read from the snapshot, so files are mutually consistent; without snapshot support fall back to a two-pass mtime/size check as in 0061's export | file coherence |
| 7 | **Secrets:** `.secrets/` (guest credential file store) is included **only** encrypted in the same set; broker-held BYO keys are **not** in the guest backup; they are backed up by the node agent as a separate encrypted object under the same customer recipient key (or the customer re-enters them: open) | key handling |
| 8 | Build a **manifest**: `customer_id`, `vm_id`, `vol_id`, `product`, schema version, image digest, time, `consistency` (`quiesced`/`crash`), per-file sha256, `coverage` and `omissions` with reasons (0064), counts. The manifest is signed/authenticated (MAC under a key the restore role knows) so a tampered manifest is rejected | I6 |
| 9 | Stream-compress and **encrypt on the VM** (never write plaintext to the off-instance path or to a temp file outside the encrypted volume) | baseline |
| 10 | Upload with the write-only credential; read back size and checksum; store `backup_set` row with `verified_at` only on success | F12 |
| 11 | Resume admission | |
| 12 | Failure at any step: abort, remove partial objects the credential can remove (it cannot; they are lifecycle-expired), alert, keep the previous restore point | |

### 18.3 Retention and expiry (proposals, owner values)

Daily sets for 14 days, weekly for 4 weeks, no monthly in the pilot (cost); maximum object lifetime 35 days; the
customer-visible statement is "recent backups for up to about five weeks; after deletion, remaining encrypted copies
expire within that period and are unreadable earlier because the key is destroyed". Pre-update and pre-delete sets
follow the same maximum. Long retention is a plan option, not default.

### 18.4 Restore (quarantine first)

1. Restore into a **quarantine volume** on a staff-controlled environment or a spare VPS (never directly over the live
   volume): decrypt with the restore role, verify the manifest MAC, `customer_id`/`vm_id`/`vol_id` match the target
   (or an explicit re-home operation recorded for disaster recovery, with a new `vol_`), verify file hashes, run
   `PRAGMA integrity_check` on every database, check the product marker. Only then attach/swap, journalled as
   `restore_product_state` with staged commit and no partial mutation (0064 H6 lessons).
2. The live volume is preserved read-only until the customer confirms.
3. **Restore drills**: scheduled automated drills on staff-owned environments, monthly in the pilot (proposal). Until a
   drill has passed, every backup is recorded as **untested**. **No drill has been run.**

### 18.5 Export (customer-initiated, step-up required)

- Produced by the runtime inside the guest from the same quiesced snapshot machinery (so it is coherent), as one
  archive: `manifest.json` (per-file hashes, coverage, omissions, `changed_during_export`), `memory/` pages as
  Markdown (0061 export format), `home/` files, settings without secrets, job history as JSON Lines, a `README`
  stating what is not included (secrets, browser logins which may not be portable, third-party vendor copies, actions
  taken elsewhere).
- Delivered through the tunnel and gateway as a single-use, time-limited download (the guest has no egress to a
  public link); optionally encrypted to a passphrase the customer chooses. Size cap with chunking. The export is not
  a backup and does not touch the backup store.
- Logged as metadata (time, size, result); never the content.

### 18.6 Deletion propagation

Section 11 (D1-D8). Backups: crypto-erase by destroying the private-key wrap, object deletion where the lock allows,
and the stated maximum expiry. Deletion of **one memory page or file** (ADR-0061) does not rewrite old backups; the
customer is told that old backup sets may still contain it until they expire (0061 deletion-matrix honesty; the same
"residual list per store").

## 19. Logging without content, monitoring, incident response

### 19.1 Logging

- **Operator logs** use a schema **allow-list** of fields: `customer_id`, `vm_id`, timestamp, event code, severity,
  duration, byte counts, image digest, error class, counters. Free-text messages from the guest are not forwarded.
  Never: prompts, replies, file names or paths, URLs with query, page text, email addresses of third parties,
  keystrokes, screenshots, cookie names. A CI test of the schema (control, NOT RUN) rejects an unknown field.
- **Content logs** stay on the volume (`logs/`), redacted (`core/redact.py` as a floor), and are exported/deleted with it.
- **Egress logs:** destination host/IP, port, bytes, outcome. **Proxy logs on the host** are rotated short (e.g. 7-14
  days, owner value) and are metadata only. The broker logs `customer_id`, model id, token counts, cost units, status;
  never prompts.
- Audit store (break-glass, suspend, STOP by staff, key operations) is separate, append-only, longer retention.
- Retention for operator logs is fixed and short; the retention values are proposals.

### 19.2 Minimal monitoring and alerts

| Signal | Alert condition (proposed, unmeasured) |
|---|---|
| Tunnel heartbeat per VM | down > 5 min |
| Guest/runtime health | health failing 3 times; restart loop |
| Disk usage on volume/host | > 80 %, > 90 % |
| Backup freshness and verification | last verified set older than 36 h; verification failure |
| Restore drill age | older than 35 days (while pilot) |
| Update ring state | gate failure, VPS on revoked digest, version lag > N days |
| Auth anomalies | refresh-token reuse, many failed logins, recovery requests |
| Egress anomalies | bytes/connection rate, distinct hosts/hour, port-scan pattern, SMTP attempts |
| Broker/budget | reserve/settle mismatch, drift (ADR-0066 20.9), global breaker state |
| Cert/credential expiry | < 7 days; rotation partial |
| Orphan resources | provider inventory vs DB mismatch |
| KMS / control plane health | unreachable; elevated error rate |
| STOP outcomes | any `still_active` or `unknown` > 2 min |

Who is paged and when is **open** (one operator: a phone push and a daily digest; no 24/7 promise in the pilot, which
the terms must say).

### 19.3 Incident response and breach notification (stubs; timelines and roles to confirm with a qualified professional)

Roles (pilot may be one person wearing all hats, which is a risk): incident commander, communications, forensics/
technical lead, legal/privacy contact. Severity: S1 (cross-customer exposure, control-plane or KMS compromise, key
leak), S2 (single-customer compromise, data loss), S3 (availability only), S4 (near miss).

Runbook stubs (text only; none rehearsed):
- **Single VM compromise:** STOP level 3 (egress cut, pause), snapshot encrypted for forensics, rotate `bundle_gen`,
  revoke broker token and BYO keys marked re-confirm, notify customer, review logs, reinstall from the signed base
  and restore from a verified earlier set into quarantine.
- **Control plane compromise:** revoke all sessions, freeze provisioning and updates, rotate signing and tunnel CA
  and KEKs (KMS), force re-enrollment; assume the routing table and metadata are exposed.
- **Cross-tenant exposure (I-violations):** identify scope by `customer_id` from metadata, preserve evidence, notify.
- **Vendor/provider breach:** follow the sub-processor contract; assess which data the vendor holds.
- **Abusive customer:** section 20.2.
- **Notification workflow:** when personal data is involved, the legal position may require notice to the supervisory
  authority without undue delay and, where feasible, within **72 hours** of becoming aware, and notice to affected
  data subjects if high risk (GDPR Arts. 33-34, as in 0065); the controller/processor split decides who notifies whom
  (processor notifies controller without undue delay). **These timelines, who the authority is, and the exact duties
  must be confirmed by a qualified professional before the pilot starts**; this ADR is not legal advice. Customer
  notice template, evidence handling, post-incident review within a stated time, and a record of all breaches are to write.

## 20. Supply chain, patching, abuse

### 20.1 Supply chain and patching cadence

Pinned and hash-checked Python dependencies (`uv.lock`/`requirements` with hashes), reproducible bundle build in CI,
SBOM per bundle, vulnerability scan gate, signed bundles verified on the VPS (section 16), pinned Chromium with a
fast-patch path, minimal base, marketplace and plugin install **off** in the pilot (0065 D15). Cadence (proposals,
owner values): critical/actively exploited browser or kernel CVE -> bundle within days and a security ring; routine
security updates -> weekly staging, monthly production; host OS updates through the signed host profile; review of
the pinned Chromium monthly. Dependency provenance and the release key custody are residual risks. A server-image CI
gate is **to write** (existing `scripts/ci/*` target desktop packaging).

### 20.2 Abuse detection and suspension procedure

Detection signals are metadata only (section 14.4, 19.2) plus external reports (provider abuse notices, third-party
complaints to the abuse contact). No content inspection by default; any review uses break-glass with consent or a
defined exception (section 8).

Ladder (each step records reason, actor, time, customer notice unless law/safety prevents, appeal channel; same as
0065 11.1 with this mapping): (1) **throttle** (per-customer limits tightened at host and broker); (2) **pause egress**
(nft cut; the guest keeps running locally; customer sees why); (3) **STOP + freeze VM** (levels 2-3); (4) **suspend
account** (read-only export allowed unless illegal content); (5) **terminate** (section 11). Provider notices from
OVHcloud-class abuse desks must be answerable within their deadline (unknown; read the provider terms first) and the
operator must keep an abuse contact. Evidence preserved minimal, legally scoped, encrypted, time-limited.

## 21. Controls prepared, NOT RUN (none authorized)

Mapping and routing: unique-constraint test of I1; cross-session routing test (session of A, hostname of B); mount
refusal on wrong marker; broker token of VM A rejected for VM B; tunnel certificate of A cannot claim B; canary
string placed in A's stores searched on all operator surfaces and in B; provisioning idempotency (replay each step,
kill between steps); orphan reaper behaviour; rotation partial states; recovery waiting-period behaviour and token
family revoke; device revoke versus running job; takeover timeouts, ticket single use, and "never logged" assertion
over all logs; break-glass fail-closed audit.
Egress: probes from the guest to link-local, RFC1918, host IPs, fleet IPs, IPv4-mapped IPv6, numeric host forms,
DNS rebinding (name flipping between public/private), redirect to internal IP, SMTP ports, UDP/QUIC, IPv6 reachability;
connection and byte caps; proxy log schema.
Containment: uid/capability/`NoNewPrivileges` inspection; no sudo; Chromium sandbox active (not `--no-sandbox`) as
non-root; fork bomb, memory bomb, disk fill with the host and broker unaffected.
STOP: cgroup kill of a double-forking, `setsid` process and a browser tree; `still_active` forced by an uninterruptible
process; `unknown` forced by a silent guest agent; level 3 with a hostile guest; verification via both observations.
Backups: SQLite backup under a WAL-hammering writer across two databases; browser profile copy with the browser open
and closed; encryption before upload (no plaintext off the volume, assert by scanning temp paths and network
payload); write-only credential cannot delete/list others; manifest tamper and wrong-customer restore refused; restore
drill into quarantine; export coherence; deletion completeness including expiry list.
Updates: signature rejection, downgrade rejection, A/B rollback, gate halt, update refused with no verified backup.
Logging: schema allow-list rejects an unknown field.

## 22. Residual risk (explicit)

| # | Risk | Why it remains | Mitigation now | Owner decision / next step |
|---|---|---|---|---|
| R1 | Nested virtualization not available or slow on the VPS | unverified | decide with the provider's written statement before purchase | section 0.2 |
| R2 | Hypervisor or kernel 0-day escapes guest to host | all software has bugs | patch cadence, minimal guest, one customer per host (blast radius = the customer's own VPS) | accept |
| R3 | Provider can read disk and RAM; operator holds KMS | mode 1 and any hosted VM | no "only you can read it" claim; confidential computing not assumed | choose mode 2 tier or accept |
| R4 | Hostile or injected agent misuses logged-in sessions, spends budget, exfiltrates files to permitted destinations | the product purpose | approvals, caps, egress rate limits, STOP | accept, explain to customers |
| R5 | Open-web egress tier cannot block all abuse (low-rate spam, scraping) | PC-like browsing | rate limits, abuse ladder, deny sets | choose tier (a)/(b) |
| R6 | Email-only recovery is a weak root of trust | convenience | 72 h wait, notifications, blocked sensitive actions | require passkey/TOTP at signup |
| R7 | Single operator: no two-person rule, no 24/7, key custody concentrated | pilot size | consent-based access, immutable audit, delayed disclosure | accept for pilot, revisit |
| R8 | Chromium without `MemoryDenyWriteExecute`, sandbox depends on user-namespace policy | JIT and sandbox needs | AppArmor `userns` profile for Chromium only | measure on the chosen image |
| R9 | Cross-database/browser-profile coherence when a long task blocks quiescence | jobs can run for hours | set flagged `crash`/`inconsistent`, never claimed coherent | decide max wait before forcing a pause |
| R10 | Backup objects live up to the expiry after deletion | object lock/retention | key destroyed, expiry stated | choose lock maximum |
| R11 | Provider-side copies and logs of the VPS (provider backups, replication) | outside our control | reinstall then destroy; statement to customer | read provider terms |
| R12 | Host proxy does not decrypt TLS: cannot see exfiltration content | privacy trade-off | metadata-only detection | accept |
| R13 | A kill can leave half-written plain files | no transactional FS | SQLite recovers; backups | accept |
| R14 | STOP cannot recall external effects | physics | stated in UI and terms | accept |
| R15 | Orphaned or mistakenly destroyed VPS (cost or data loss) | automation errors | tags, confirmation for orphans, no auto-destroy of non-empty volumes | accept |
| R16 | IPv6 disabled may break some sites | v1 simplification | revisit | owner |
| R17 | Release signing key compromise | any signing scheme | offline key, ring rollout, revocation list | custody plan |
| R18 | Unmeasured thresholds, retention values and limits in this ADR | no run, no data | proposals only | measure in pilot |
| R19 | Engine vendors receive customer prompts regardless | the product design | ADR-0050 privacy gate, DPAs | legal |
| R20 | Legal duties (breach timelines, roles, AUP of the provider) not reviewed | not legal advice | stubs | qualified professional |

## 23. Decisions

**Embedded here as the design (consistent with the baseline; changeable by the owner):** outbound-only tunnel and
no inbound ports; routing by session -> customer -> VM -> tunnel subject; credential bundle with generation rotation;
broker outside the guest; operator-metered keys only in the control plane; takeover with step-up, one-time ticket,
10-minute proposal, agent input pause, metadata-only logging; revocation is not STOP; recovery wait and re-confirm of
BYO keys; host-side authoritative egress with proxy-side resolution, pinning and per-hop redirect re-validation;
IPv6 off in v1; non-root with Chromium sandbox preserved; signed A/B bundles with ring rollout; STOP level 2 by
cgroup kill with two-observation verification mapped to the StopController outcomes; write-only, encrypted-on-VM,
off-instance backups with quiesce and quarantine restore; allow-list log schema; provisioning as an idempotent
state machine with reaper.

**Left to the owner:** (1) confirm nested virtualization on the VPS in writing (R1) or accept the weaker fallback;
(2) egress tier (a)/(b) and the abuse thresholds; (3) key mode 1 vs 2 and the "no recovery" tier; (4) off-instance
backup store, retention numbers, object-lock maximum; (5) takeover limits, maintenance windows, canary volunteers;
(6) whether any customer-visible promise on operator access is made (recommended: none beyond audit); (7) who is paged
and the pilot's availability statement; (8) legal review (roles, DPA, breach timelines, provider AUP); (9) whether a
customer may skip passkeys (R6); (10) when and whether to authorise the controls in section 21.

## 24. Unverified (read this before relying on the document)

Nothing was executed or fetched. Not verified: OVHcloud VPS-2 specifications, nested virtualization, firewall,
metadata/management ranges, console, backup/snapshot options, object-storage options, abuse-desk rules, billing
behaviour of stopped or reinstalled VPSes; Chromium sandbox behaviour in the chosen image; systemd and kernel feature
levels (e.g. `cgroup.kill` needs a kernel with it); SQLite and filesystem snapshot behaviour under load; all numbers
(timeouts, retention, thresholds); legal statements. Existing code (`core/product_backup.py`, StopController,
`surface_security`) was read, not run, and none of the new components (node agent, broker, egress proxy, gateway,
tunnel, cgroup probe, backup agent) exist yet.

## 25. Sources (public documentation, NOT fetched in this session; to re-verify before relying on them)

- systemd resource control and `KillMode`/`systemctl kill`: https://www.freedesktop.org/software/systemd/man/latest/
  (systemd.kill, systemd.resource-control, systemd.exec)
- cgroup v2 `cgroup.kill` and `cgroup.events`: https://docs.kernel.org/admin-guide/cgroup-v2.html
- Chromium Linux sandboxing and user namespaces: https://chromium.googlesource.com/chromium/src/+/main/docs/linux/sandboxing.md
- SQLite online backup API: https://www.sqlite.org/backup.html ; `VACUUM INTO`: https://www.sqlite.org/lang_vacuum.html
- nftables: https://wiki.nftables.org/
- OWASP SSRF prevention: https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html
- GDPR Arts. 28, 32, 33, 34: https://eur-lex.europa.eu/eli/reg/2016/679/oj
- OVHcloud VPS documentation and terms (to read before any purchase): https://www.ovhcloud.com/en/vps/ and its help
  centre; legal terms for the service and acceptable-use/abuse rules.

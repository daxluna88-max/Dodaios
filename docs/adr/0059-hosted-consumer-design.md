# ADR-0059 — Hosted Consumer (about 60 EUR per month): design reference

**Status:** Proposed (design and static review only: no SaaS implemented, no provider chosen or activated, nothing
tested, no spend) · **Date:** 2026-10-27 · **Related:** ADR-0049, 0050, 0053, 0054, 0055, 0056, 0058

## 1. Reference and what it is not

Dax's reference (issue 16, 2026-10-27): about **60 EUR per month total customer spend** to use Jarvis Consumer in the
cloud from a phone/app or a web interface, without a PC. It is a design and commercial reference, **not an authorized
spend and not a validated price**. Consumer on the PC and hosted Consumer are two *distributions* of one product;
Consumer and Work stay two independent products (ADR-0054).

**Work (PMI) is unchanged:** local inference only, no cloud fallback (ADR-0049/0053). Nothing here alters its policy.
A hosted offer for Work, if ever wanted, is a separate decision: a private cloud is not "local in the company" and
not offline.

Hypothesis, to be checked without paid tests (none of it is demonstrated):

| Item | Indicative share | Note |
|---|---|---|
| Hosting, memory, backups | 10 EUR | depends on isolation model (§4) and storage (§7) |
| AI engines and voice, at usage | 20 EUR | depends entirely on usage quantities (§6) |
| Operation, support, margin | 20 EUR | not an estimate of real support load |
| Reserve for usage swings | 10 EUR | |

VAT, payment fees, support, security work and real consumption go into a future profit-and-loss. **No margin is
guaranteed.** Preliminary target: hosting + AI within **25-30 EUR per customer per month on average**, a goal and not
a result. **The 60 EUR is not economically validated**: unit prices, real usage, support load and
margin are all still to be measured or documented.

## 2. What depends on the desktop today, and what hosting needs

Read in code, not run. "Reuse" means the logic is plausibly reusable behind an adapter; it is not a claim that it
runs in a server today.

| Dependency today | Where (read) | Reuse | Missing for hosting |
|---|---|---|---|
| One owner, one process | `core/instance.py`, `control_auth.py` (per-user Bearer key), `launch` locks | brain logic, privacy gate, orchestrator (ADR-0050/0055), personality/mood (pure), STOP coordinator (pure) | tenant id on every call, path, query, job, cache; no process-wide singleton holding customer state (the STOP controller, kill switch, turn ledger, style runtime are per process today) |
| Loopback / local session | `surface_security.py`, `assert_bind_safe` (already fails closed without a key) | Host/Origin/CSRF logic | internet-facing authentication: accounts, sessions, recovery, rate limiting, device pairing (the phone companion, Dodaios #4, is local-network oriented) |
| OS keyring | service per product (`dodaios-consumer`) | none | a per-tenant server-side secret store; customer-supplied keys never shared; no OS keyring on a server |
| Filesystem data dir, SQLite | `core/paths.py` (`user_data_dir`), board/agentic IDE/memory wiki stores | storage schemas | tenant-scoped storage, per-tenant backup, export, deletion across files, indexes and caches; SQLite concurrency limits |
| Local audio device, STT/TTS | `audio/player.py`, `speech/pipeline.py`, local voice routes | provider adapters for STT/TTS | streamed audio from app/web, server-side metering, latency/echo budget, a verified stop of playback (not found today, ADR-0058) |
| GUI and computer use | desktop window, tray, computer-use loop, screen context | the browser UI (React) can be served | **most local-computer tools are meaningless or dangerous on a shared server** (files, apps, browser, screen) |
| Local engines | Ollama / local-openai | engine adapters | no local model per customer; the operator's engines and metering instead |
| Subscription logins of vendor CLI tools | agent accounts, CLI runtimes | none | a subscription is not API credit and its terms may not allow resale or server use (issue 16, D08): **not assumed** |
| Cost control | costs module, `[brain.multi_engine]` budget | budget structures | a per-tenant ledger enforced in the server (§6) |
| Updates and operations | desktop auto-update | none | deployments, migrations, monitoring, incident handling, consented support access with an audit log |

Phone access without a PC needs: a web/app front end over an authenticated API, text first (§5), pairing/session
management, notifications for decisions waiting (ADR-0058), and nothing that assumes a local microphone, window or
file system. **No control of other apps on the phone is promised** without a supported platform integration (none is
assumed or designed here).

Deploying today's desktop runtime as a service as it is would share state between customers and run local-computer
tools on the operator's machine. It must **not** be done.

## 3. Alternatives for isolation

| | A. Instance per customer | B. Shared runtime, isolation by design |
|---|---|---|
| Idea | one dedicated process/VM group per customer | one multi-tenant runtime; tenant id everywhere |
| Isolation | strong boundary by construction | depends on every code path honouring the tenant (large audit surface: singletons, caches, SQLite, logs) |
| Cost at ~60 EUR | high fixed cost per customer; idle capacity; likely above the 25-30 EUR target for light users | low marginal cost; economies of scale |
| Fit with today's code | closest (the runtime is one-owner); least change | most change (see §2) |
| Risk | cost, operations at N instances | cross-tenant leakage, noisy neighbour, one bug exposes all |

**A container is not proof of safe isolation for arbitrary tools or code.** Containers share a kernel; running
untrusted or model-driven code for different customers on one host needs a stronger boundary (for example a
lightweight VM or a hardened sandbox) plus no host access, no shared file system, and egress denied by default.
Proposal to evaluate, not decide: a shared control plane (accounts, quotas, routing, ledger) with **per-customer
isolated workers** for anything that executes tools; a **text-only base plan without code or local-computer tools**
at first, which removes the hardest isolation problem. Dedicated installation remains a possible higher offer. No
dedicated GPU per customer in the base plan.

## 4. What is isolated per customer, and how it ends

Per customer, separately: memory (pages, search and embedding indexes), credentials, jobs and their approvals, files
and uploads, logs (with the customer's content kept out of operator-wide logs), backups, session overrides (mood,
style), and the **STOP scope**: STOP of one customer's session stops that customer's jobs and nobody else's (the
per-product process scope of ADR-0058 becomes per-tenant here; the idea is the same, the unit changes).

- **Authentication and revocation:** accounts, device sessions that can be revoked individually; revoking access does
  not cancel an agent already running, so revocation and STOP are two actions.
- **Quotas and egress:** per-customer caps on requests, concurrency, storage and uploads; outbound traffic from a
  customer's worker denied by default, with an allow-list per destination category.
- **Export and deletion, with limits stated to the customer:** export of memory and files in a readable format;
  deletion that names what it covers (primary store, indexes, caches) and what remains until it rotates (backups).
  "Archived" is never called "deleted" (ADR-0058).
- **Memory lives on the server.** It is never described as "local on your device". **Encryption at rest does not
  make data invisible to the operator during processing**, and no text may promise that.

## 5. Text first, voice later

- **Initial path (text):** phone/web client → authenticated API → control plane (auth, quota check, ledger
  reservation) → privacy gate (ADR-0050) → engine → answer. Declared destinations: the hosting region, the engine
  vendor(s) chosen for the turn. Multi-engine answers (ADR-0055) count every branch and the synthesizer against the
  same budget.
- **Voice (future, own decision):** audio capture on the phone/web, streaming transport, STT, engine, TTS, playback,
  with each component and its destination listed, per-minute metering, and a verified stop. Not designed in detail
  here.
- **Privacy gate toward remote engines and in synthesis remains** (minimum authorized context; ADR-0050/0055). A
  provider region or a data-processing agreement is a fact to document, not an assumption.

## 6. Reference 60 EUR: scenarios, quantities, caps

All numbers are **hypotheses to be replaced by measured usage and documented unit prices**; no unit price below is
documented and none is invented. The scenarios state the quantities that decide the cost.

| Quantity | Light | Medium | Intense |
|---|---|---|---|
| Text turns per day | ~10 | ~40 | ~120 |
| Share answered by several engines (ADR-0055) | 0 % | ~10 % | ~30 % |
| Voice minutes per month | 0 (text only) | ~60 | ~300 |
| Stored memory + files | <0.5 GB | ~2 GB | ~10 GB |
| Concurrent turns per customer | 1 | 2 | 3 |
| Tools that execute code | none | none | not in the base plan |

Cost per scenario = (tokens per turn × turns × unit price of the chosen engine(s)) + (voice minutes × STT/TTS unit
price) + (storage × storage price) + (share of fixed hosting). **Unit prices are not documented here;** they must be
taken from each vendor's published price list at decision time, with VAT, payment commissions, support and margin
added separately. Result expected to differ widely between scenarios: the intense one may not fit 25-30 EUR, which is
a reason for caps (below) rather than a failure of the idea.

**Technical caps, applied in the server, not advisory:**
1. Per-customer budget per period in the plan's unit (tokens/minutes/requests) and in money where the unit price is
   known.
2. **Concurrent reservations:** before a call, reserve its worst-case cost against the customer's remaining budget
   atomically; concurrent turns, multi-engine branches and voice each reserve; a call that cannot reserve is refused
   before it starts.
3. **Unknown cost is not zero:** a call with an unknown price is refused unless explicitly allowed by the owner of the
   account; estimates are shown as estimates.
4. **Reconciliation:** after the call, replace the reservation with the metered amount; periodically reconcile the
   ledger with the vendor's own usage reports; a mismatch above a threshold blocks new spend for that customer until
   reviewed.
5. **Block before new spend:** when the remaining budget is below the next reservation, new work stops with a plain
   message. **No automatic overage, no automatic top-up.** A raise is an explicit choice by the customer.
6. **No absolute-cap promise** where a vendor cannot enforce a hard limit: the product text says the cap is applied by
   Jarvis from its own ledger, which can lag a vendor's real billing.
7. Usage and remaining quota are shown to the customer in plain words (ADR-0058 strip, extended).

## 7. Hosting candidates to compare (none chosen, none activated, none certified)

Hetzner (EU region) for hosting; Scaleway for possible European inference; OVHcloud as an integrated alternative;
Infomaniak and Aruba to compare. For each, **still to verify** (not asserted here): data location and who can access
it; data-processing agreement and subprocessors; jurisdiction and legal-access exposure; log and backup retention and
deletion guarantees; what isolation primitives are offered (VM, dedicated host, private networking); egress control
and traffic costs; availability of GPUs or hosted inference and its terms; billing caps or alerts the provider can
actually enforce; support access to customer data; certifications actually held; price lists and minimum commitments.
No compatibility with this runtime is certified.

## 8. Open questions and controls

- Which isolation (A/B or hybrid) and which sandbox technology; who audits cross-tenant leakage.
- Whether vendor subscriptions or customers' own API keys may power a hosted instance (terms of service).
- How the hosted offer relates to the phone companion (Dodaios #4).
- Legal form, privacy notice, retention, processing roles: out of scope here and needed before any customer.
- To catalogue in issue 16 as NOT RUN when this design is agreed: tenant isolation (data, memory, credentials, jobs,
  logs, backups), per-customer STOP and revocation, budget reservations under concurrency, reconciliation and block,
  export and deletion completeness, egress denial, restore from backup, first-party and third-party access audits.
- Nothing here authorizes spend, a purchase, a deployment or a customer-facing promise.

## Addendum (2026-10-28): scope extended to a PC-like environment

New owner requirements (a per-customer Linux desktop-like environment with a real browser, shell, GUI automation and
long tasks; low cost through idle hibernation; provider-neutral) change section 3: the "text-only base plan without
code or local-computer tools" mitigation no longer applies. Runtime audit, per-customer instance vs shared runtime,
node image, customer isolation, security, abuse, backup, STOP and the provider requirement checklist are in
[ADR-0065](0065-jarvis-cloud-runtime-isolation-security.md). Design and static review only; nothing run.

## 9. Follow-up

Server-enforced budget ledger design (reservations, accruals for the per-customer environment, reconciliation, circuit
breaker; static module `dodaios/cloud/budget_ledger.py`, written-not-run tests) and the mobile cloud interface
(onboarding, consumption display, memory location, live-view option, notifications, accessibility, screen/API
inventory): see `docs/adr/0066-jarvis-cloud-budget-and-mobile.md`. Nothing was run; no provider chosen, no price fixed.

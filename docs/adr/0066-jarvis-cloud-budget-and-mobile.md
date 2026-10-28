# ADR-0066 - Jarvis Cloud: server-enforced budget (block 20) and mobile cloud interface (block 22)

**Status:** Proposed. Design plus ONE static code seam (`dodaios/cloud/budget_ledger.py`) and written tests.
**NOTHING WAS RUN:** no pytest, no build, no service, no network, no provider call, no spend, no download. Only
`python3 -m py_compile` and `ruff check` (never `ruff format`) were executed. No frontend source was edited, so there is
nothing to type-check and no `dist` to rebuild; **no UI is live**. No provider is chosen, no price is fixed, nothing
is wired into the running app. **Date:** 2026-10-28. **Related:** ADR-0050 (Budget), 0052, 0058, 0059 (hosted design,
section 6), 0062 (phone panel), 0063 (Italian UI), `docs/mobile-panel.md`.

Legend per item: **designed** (text only), **implemented-static** (code or tests written, never run), **open**
(decision or fact missing). "Controls prepared, NOT RUN" are listed at the end of each block.

## 0. Owner requirements this ADR must honour

1. The hosted assistant behaves like a **physical PC for the customer** (files, a real browser, a shell, GUI
   automation). So the cost is **a per-customer environment (compute, storage, egress) PLUS AI usage (and voice
   later)**, not tokens alone. A running environment costs money even when nobody talks to it.
2. It **must cost little**: idle must be cheap by design (suspend), caps are small by default, heavy features are
   opt-in.
3. **No automatic top-up, no overage.** Only the customer (or the operator, audited) raises a cap, explicitly.
4. **Cost is never invented: unknown price is not zero.** Unknown => refuse, or an explicit owner-set estimate that
   is labelled as an estimate everywhere.
5. **Provider not chosen**; others compile the list. Everything below is provider-neutral and uses abstract integer
   amounts. Provider facts that matter (hard caps, usage-record latency, suspend billing) are listed as open
   questions to ask each candidate, not assumed.

---

# BLOCK 20 - Budget enforced by the server

## 20.1 Principle and what it can and cannot promise  (designed)

The budget is enforced **by Jarvis's own server before any paid call starts**, from a ledger it controls. It is a
**fail-closed gate**, not a mirror of the provider's bill.

- **What it guarantees:** Jarvis will not *start* a paid call whose worst case does not fit the customer's remaining
  budget, and will not start any call when a cap, a block or the breaker says stop. Concurrent calls cannot jointly
  pass a cap (atomic reservation).
- **What it does NOT guarantee:** an absolute ceiling on what a provider eventually bills. Reasons that cannot be
  closed from our side: a provider that offers **no hard spending cap** (some only alert, some bill the account not the
  customer); usage records that arrive late or in coarse units; time-based costs that accrue while we decide;
  work a provider keeps running after we cancel; price changes. Product text must say: "Jarvis applies your limit from
  its own accounting, which can lag the real billing slightly". It must **never** say "you can never pay more than X"
  unless a provider-side hard cap that is actually enforced and tested exists for that cost class (open, 20.12).
- Mitigation instead of a promise: reservation of worst case, a safety margin inside the cap (the operator sets the
  customer cap below the amount the business can lose), reconciliation with a block on drift, and a global breaker.

## 20.2 Cost classes and what "per-customer environment" adds  (designed, enforced-static)

| Class | Kind | How it enters the ledger | Stops by |
|---|---|---|---|
| `ai_inference` | per call, variable | reserve worst case, settle with measured tokens/units | refusing new calls |
| `voice` | per call/minute (later, 22.9) | reserve per segment or per minute window, settle with measured | refusing new sessions/segments |
| `compute` | **time based**: environment hours while awake | `record_usage` from provider usage records or the worker's own wake/sleep timer | **suspend the environment** (see 20.3) |
| `storage` | time based and size based | accrual at fixed intervals (`record_usage`), plus a hard size quota on writes | refusing writes above quota |
| `egress` | volume based | accrual from provider/network counters; per-customer egress quota | throttling/denying outbound |

Only `ai_inference` and `voice` are naturally "before the call". Compute, storage and egress are **accruals**: the
money is spent while the environment exists. Therefore the cap alone is not enough; the control plane must also
**act on the block** (20.3). The ledger exposes `usage(...)["blocked"]`; the environment manager reads it.

## 20.3 Hard stop when the cap is reached  (designed; reservation side implemented-static)

When remaining budget cannot cover the next reservation, or accruals reached the cap:

1. New paid calls are refused **before they start** with a plain Italian message (`MESSAGES_IT` in the module).
2. The environment manager receives the block and **moves the customer's environment to the cheapest state**
   (suspended / hibernated; what that costs per provider is open, 20.12). The customer's files and memory stay
   readable and exportable; STOP, export and delete keep working (they cost ~nothing and must never be blocked by
   the budget, same rule as ADR-0058: STOP does not depend on the budget).
3. **No overage and no automatic top-up.** There is no code path that raises a cap by itself. `set_cap` needs an
   `actor` and writes an audit row; the customer-facing "raise my limit" is an explicit confirmed action with the
   amount shown, in the customer's own billing flow (open, 22/payment).
4. A loop already running is not killed by the ledger alone: the agent loop checks reservation on every paid
   step, so it stops at the next step. The environment suspension is what stops un-metered compute.

## 20.4 Reserve -> settle / refund  (implemented-static)

`BudgetLedger.reserve(customer, cls, amount, idem=, period=, scope=, ttl_s=, owner_estimate=)`:

- One `BEGIN IMMEDIATE` transaction does: idempotency lookup, breaker, customer block, cap lookup (no cap => refuse),
  per-call ceiling, total cap, class cap, global cap, scope ceiling, insert. So N concurrent turns/branches (ADR-0055
  multi-engine counts every branch and the synthesizer) cannot jointly exceed the cap. Same pattern as
  `safety/effects.py`.
- Held money = `reserved` and `unknown` rows at their **worst case** + `settled` rows at their **real amount**.
  A released row holds nothing.
- `settle(res_id, actual, measured=)` replaces the hold with the measured amount (the refund of the unused part is
  implicit: the hold shrinks to `actual`). An actual **above** the reservation is recorded as it is (field `overrun`),
  never clipped: clipping would invent a cheaper truth. The next reservation then sees less room.
- `release(res_id)` only for a call that **provably never started** (e.g. refused by a later gate before the
  provider was contacted).
- Amounts are **integers in one minor unit** chosen by the caller. Floats, bools and negatives are refused
  (`invalid`). No currency, no price list in the module.

## 20.5 Unknown price  (implemented-static)

`amount=None` means "price unknown". Result: `unknown_price` refusal, **never zero**. The only way through is an
`owner_estimate` integer set explicitly by the account owner in configuration; the row is stored with
`price_basis='owner_estimate'` and is reported as estimated until a measured settle replaces it (and if the call ends
with unknown usage the row stays `unknown`, holding the estimate). Operator-wide default estimates are not provided
on purpose.

## 20.6 Idempotent settle  (implemented-static)

- Same `settle(res_id, same actual)` again: `replay`, no change. A **different** actual: `conflict`, no change
  (a retry cannot rewrite history). `settle` after `release`: `conflict`.
- `reserve` with the same `(customer, idem)` returns the existing reservation (`replay`); same key with a different
  class/period/scope: `conflict`. The `idem` is derived by the caller from `trace_id + provider call id`, like the
  effect ledger key.
- `record_usage` is idempotent by `(customer, idem)` as well (a usage record imported twice counts once).

## 20.7 Crash recovery of dangling reservations  (implemented-static)

A worker that dies between reserve and settle leaves a `reserved` row. `recover()` turns every reservation past its
deadline into **`unknown` and keeps it holding** its worst case: the call may have run and been billed. It is
resolved only by (a) `settle` with a provider-reported amount (reconciliation), or (b) `release(operator=True)` by an
operator who confirmed the call did not run. Timeout never silently "releases", because that would let a crash loop
spend without bound. A periodic job calls `recover()`; the customer screen shows held-unknown amounts as "in verifica".
The TTL must exceed the longest allowed call, or long calls renew by re-reserving under a new idem (open: renewal API).

## 20.8 Per-call and per-step ceilings for agent loops  (implemented-static; wiring designed)

- `set_call_ceiling(cls, amount)`: no single reservation of that class may exceed it (`call_ceiling`).
- `open_scope(scope, customer, max_amount, max_calls)` per agent run (one scope per mission/loop run): the sum of
  everything charged in the scope and the number of paid calls are bounded (`scope_ceiling`). A loop stuck in retries
  is stopped by the scope even when the monthly cap is far away.
- Designed, not built: scope defaults by task kind (chat turn, research, GUI-automation run), a per-step
  ceiling inside long GUI loops (screenshot + model per step), and the user-visible "this task reached its limit,
  continue?" which is an **explicit new scope**, never an automatic extension.

## 20.9 Reconciliation and drift  (implemented-static for the comparison; the data feed is open)

- `reconcile(customer, period, cls, provider_amount, tolerance)`: compares the ledger (settled + unknown, **not**
  live reservations) with a provider figure. Provider **above** ledger beyond tolerance => the ledger under-recorded:
  the customer is **blocked** (`customer_blocked`) until `clear_block(actor, note)`. Ledger above provider => kept
  (conservative), flagged `review_over_recorded`, no block. Every comparison is stored in `recon`.
- `reconcile_global(period, cls, provider_amount)`: provider invoices/usage are usually per **account**, not per
  customer, so the operator-wide comparison is the realistic one; drift above tolerance **trips the circuit breaker**.
- Open: which provider exposes usage records, at what latency and granularity, whether they can be tagged per
  customer (labels/projects) at all, and what tolerance is acceptable. Until that is known the comparison runs on
  whatever figure an operator imports, and the product shows "stima/misura" honestly (22.5).
- Late usage records for a past period: recorded into that period via `record_usage(period=...)`; they can push it
  over the cap, which only affects that period's remaining (never a retroactive charge to the customer beyond the plan:
  who bears the loss is a business decision, open).

## 20.10 Operator-level global circuit breaker  (implemented-static)

- `trip_breaker(reason, actor)` / `reset_breaker(actor, note)` (note required): while tripped, **every** new
  reservation is refused with `breaker` (replays of existing reservations and settles still work, so in-flight calls
  can finish and be accounted). Automatically tripped by global drift (20.9).
- `set_global_cap(period, cap)`: sum over all customers; reservations beyond it are refused (`global_cap`).
- Designed: automatic trip rules (spend velocity per minute, error-rate of settles, provider outage), and a manual
  big red switch for the operator. The breaker never deletes customer data and never blocks STOP/export/delete.

## 20.11 Low cost by design  (designed)

- Default plan has **small caps**, the customer chooses to raise them (no default of "use what you need").
- **Idle environment suspends** after a short inactivity (value open); wake on demand costs a visible delay, which
  the screen states. Persistent state lives in storage, not in a running machine.
- Heavy capabilities (live desktop stream, GUI-automation loops, voice, multi-engine) are separate opt-ins with their
  own class cap, so a customer can use text only and pay little.
- Storage quota small and visible; large files expire unless kept.
- Cheapest-capable engine first, expensive ones only by explicit choice; multi-engine fan-out counted fully (ADR-0055).

## 20.12 Open questions for the provider list  (open)

For each candidate (do not assume): is there a **hard** spend cap or only alerts; how fast do usage records appear and
can they be labelled per customer; is a **suspended** environment billed (compute vs disk vs reserved IP); minimum
billing increments; egress pricing and free allowance; API to suspend/resume/delete programmatically; per-customer
isolation primitive (VM vs container); tax/currency handling. Fix the minor unit and currency only when a provider and
a plan are chosen.

## 20.13 Wiring (not done)  (designed)

`ConsumerOrchestrator.Budget` (ADR-0050) is the in-memory, per-fan-out ceiling and stays. In the hosted service a
`LedgerBudget` adapter would implement `reserve/release` against `BudgetLedger` with the turn's `idem`, so the
orchestrator needs no change. Wiring points: engine call wrapper, STT/TTS wrapper, environment manager (accruals and
suspend), GUI-automation step loop. None is done; nothing imports the module.

## 20.14 Block 20 status table

| Item | State |
|---|---|
| Atomic concurrent reservation, reserve/settle/release | implemented-static (module + tests, NOT RUN) |
| Per-customer monthly total and per-class caps; no cap => refuse | implemented-static |
| Unknown price refused / explicit owner estimate | implemented-static |
| Idempotent reserve, settle, record_usage | implemented-static |
| Crash recovery to `unknown` (still held) | implemented-static |
| Per-call ceiling, per-scope amount + call-count ceiling | implemented-static; step defaults designed |
| Reconciliation, drift block, global reconcile -> breaker | implemented-static; data feed open |
| Global cap + circuit breaker | implemented-static; auto-trip rules designed |
| No overage / no auto top-up (no code path; audited `set_cap`) | implemented-static |
| Accruals for compute/storage/egress | implemented-static (`record_usage`); acting on block (suspend) designed |
| Customer message on stop | implemented-static (Italian strings); UI designed (22.5) |
| Provider hard caps / usage records / suspend billing | open (20.12) |
| Wiring, multi-node database, renewal of long reservations, currency/unit | open |

## 20.15 Controls prepared, NOT RUN

Tests written in `tests/unit/cloud/test_budget_ledger.py` (never executed): no-cap refusal; cap boundary; 40 threads
vs a cap that fits 10; unknown price; owner estimate; invalid amounts (float, negative, bool, NaN, string); idem
replay and conflict; settle idempotent/conflict; overrun kept; estimated vs measured apart; unknown usage held;
release rules; crash recovery and operator release; class cap and call ceiling; scope ceilings; accrual over cap then
block; breaker and reset needing a note; global cap; reconcile block/clear/over-recorded; global reconcile breaker;
reopen persistence. Also to catalogue as NOT RUN with a real provider later: invoice vs ledger drift on a sandbox
account, suspended-environment billing, provider cap behaviour at the limit, SQLite contention at target concurrency.
Executed: `python3 -m py_compile` and `ruff check` on the module and tests only (E501 is suppressed at file level in the
module for long Italian strings and SQL).

---

# BLOCK 22 - Mobile cloud interface

Reuse rule: the hosted phone UI **is the existing phone panel** (ADR-0062, `dodaios/ui/web/mobile/`, plain static JS,
no build step) pointed at a hosted server, extended with the screens below. Nothing from the desktop React bundle is
required on the phone. Where a desktop component exists it is listed as design reference (22.12), not as a dependency.

## 22.1 Access without a PC  (designed)

The customer opens a **web app / PWA** (`https://<service>/m/`) on the phone. No PC, no tunnel, no Tailscale (ADR-0062
section 5 is the *self-hosted* path). Install = "Add to Home Screen" (manifest and service worker exist). The server
is internet-facing, so ADR-0059 section 2 applies in full: accounts, sessions, recovery, rate limiting, per-tenant
isolation. The current pairing flow (code shown on a PC) **cannot be the hosted login**; it is replaced by 22.2 and
kept only for the self-hosted distribution.

## 22.2 Hosted onboarding  (designed)

Order: (1) create account (email + passkey or one-time code by email; password only if unavoidable), (2) read and
accept consent, (3) choose plan and limits, (4) payment (no card data in our pages: provider-hosted payment page),
(5) environment created (progress state), (6) first conversation. Rules:

- **No secrets in URLs**: no token, magic-link secret, session id or API key in a query string or fragment that stays in
  history/logs/referrers. Email sign-in uses a **short code typed by the person** (or a link that lands on a page which
  POSTs the one-time token and immediately redirects to a clean URL, with `Referrer-Policy: no-referrer`; open which
  of the two). Session = HttpOnly, Secure, SameSite=Strict cookie (as the panel's device cookie), CSRF header on unsafe methods.
- Consent screen states, in plain words: memory lives on the server; the engine vendors that receive the content of
  a turn; the cap and "no automatic renewal of extra spend"; that Jarvis controls only its own hosted environment;
  how to export and delete. Consent text is versioned; acceptance stored with version and time.
- Legal texts (privacy notice, processing roles, retention) are **open**, needed before any customer (ADR-0059 section 8).
- First run guide (ADR-0057) is reused in content, shortened for phone.

Wireframe (all Italian, 360 px wide):

```
+--------------------------------+
| Jarvis Cloud                   |
| Crea il tuo account            |
| Email  [______________]        |
| [ Continua ]                   |
| Ti mandiamo un codice di 6     |
| cifre. Nessun link segreto.    |
+--------------------------------+
| Prima di iniziare (1 di 3)     |
| - La memoria sta sul NOSTRO    |
|   server, non sul tuo telefono |
| - Il testo delle conversazioni |
|   va al motore AI che scegli   |
| - Limite mensile: tu decidi,   |
|   nessuna spesa automatica     |
| [ ] Ho letto e accetto (v1)    |
| [ Continua ]                   |
+--------------------------------+
| Il tuo limite del mese         |
| ( ) Leggero   [ da definire ]  |
| ( ) Normale   [ da definire ]  |
| Importi non decisi: mostrati   |
| solo quando saranno fissati.   |
| [ Continua ]                   |
+--------------------------------+
```

(Plan names and amounts are placeholders; none is decided.)

## 22.3 Connection status  (designed)

A thin bar under the header, always the same place: **Collegato** / **Connessione lenta** / **Offline** (browser
offline) / **Ambiente in pausa** (suspended to save cost, "si riattiva in circa N s" only if measured) /
**Ambiente non raggiungibile** (server side). The bar never says "online" from a stale poll: it carries the age of the
last successful reply ("aggiornato 5 s fa"). Offline queues nothing that has an effect: a message not delivered is
shown as not sent with "Riprova", never auto-resent after a long gap (avoids a duplicate effect; the effect ledger
protects the server side, ADR-0044).

## 22.4 Where memory lives; export; delete  (designed)

A fixed screen "La tua memoria": text **"La memoria e conservata sul server di Jarvis Cloud, non sul tuo telefono."**
(never "local"; encryption at rest does not hide data from the operator during processing and no text promises that).
Actions: **Esporta** (readable archive of memory pages and files; reuse the memory vault read routes `/api/wiki/*` on
the tenant, plus file list), **Correggi / archivia** a note (ADR-0061 controls reused), **Elimina tutto** (typed
confirmation; names what is covered: primary store, indexes, caches, files, environment; and what remains until it
rotates: backups, with the retention in days once known). "Archiviato" is never called "eliminato" (ADR-0058).
Deletion progress is a state (in corso / completata / parziale con elenco), same four-outcome honesty as STOP.
Deletion and export are **free of budget** (never blocked by the cap).

## 22.5 Usage / consumption display from the ledger  (designed; data source implemented-static)

Source: `BudgetLedger.usage(customer, period)`. The screen shows, per class, three numbers kept apart and labelled:

- **Misurato** (settled with measured usage), **Stimato** (settled with an estimate / owner estimate), **In sospeso**
  (reserved or unknown, "in verifica"). Never summed into one "exact" figure without the split visible. If a figure
  is unknown it says "non disponibile", never 0.
- A bar "Speso + in sospeso su limite" with text, not colour alone ("62% del limite"). A note: "Il conteggio e di
  Jarvis e puo ritardare rispetto alla fatturazione reale."
- Compute/storage/egress appear as **"Il tuo ambiente"** (time based), AI as **"Risposte AI"**, voice only once enabled.
- At the cap: banner with `MESSAGES_IT["cap_reached"]`, a button "Aumenta il limite" that opens an explicit confirm
  with the amount (never automatic), and the line "L'ambiente e in pausa per non consumare altro".

```
+--------------------------------+
| Consumi di novembre            |
| [########------] 62% del limite|
| Risposte AI                    |
|   misurato 41  stimato 3       |
|   in verifica 2  (unita)       |
| Il tuo ambiente                |
|   misurato 18  in verifica 0   |
| Archivio file    non disponibile|
| Il conteggio e di Jarvis e può |
| ritardare sulla fattura reale. |
| [ Aumenta il limite ]          |
+--------------------------------+
```

("unita" = placeholder; real units only when the unit is decided.)

## 22.6 STOP always available  (designed; panel already has it)

The panel's header STOP (`#stopall`, `POST /api/phone/stop`, StopController ordering, four outcomes per part) stays
**fixed and visible on every screen including settings, usage and onboarding after account creation**. Hosted
semantics: STOP is **per tenant** (ADR-0059 section 4): closes the door, denies pending approvals, stops that customer's
jobs, GUI-automation run, live view and voice, and states honestly "non annulla cio che e gia stato fatto". It works
when the budget is exhausted, when the environment is suspended (then "nulla in esecuzione"), and when the engine is
down. Revoking a device session and STOP are two different buttons (ADR-0059).

## 22.7 Approvals with request identity  (designed; reuse implemented in 0062)

Reuse the block 13/phone contract unchanged: each card carries `request_id`, `tool_name`, `call_fingerprint` and the
STOP `generation`; the decision echoes them; mismatch => 409 `identity_mismatch`, stale => 409 `stale_generation`;
after STOP only deny works. Hosted additions: the card names the **target** ("nel tuo ambiente cloud") and the
class of risk (file / browser / shell / GUI). For the new computer-like tools the approval default is **ask every
time**; "always allow" stays refused on the phone. An approval waiting while the app is closed is not delivered (22.10).

## 22.8 What a phone can control  (designed, stated to the customer)

Only the **hosted environment** (its files, its browser, its shell, its GUI). **Not** the phone's other apps, its
settings, its camera/contacts/notifications, and not the customer's PC. Onboarding and the help screen say this in
one sentence. The "physical PC" metaphor is: *a PC that lives in the cloud, belongs to you, and Jarvis uses it for you.*
Files move by the existing upload/download (resumable, 2 GiB per file in the panel; hosted limits are the plan's
storage quota, open).

## 22.9 Voice: text first  (designed; explicitly deferred)

**Voice is OFF in the first hosted release.** The microphone button is not shown (the panel's dictation uses the
browser's own recognizer on the phone, which is client side and not our voice path; it is hidden in the hosted build
until decided, because its privacy/destination is the browser vendor's). Voice appears only **after** the path is
completed and reviewed: capture, streaming transport, STT, engine, TTS, playback, a verified stop, per-minute metering
in the `voice` class with its own cap, and the destination of each component listed (ADR-0059 section 5, ADR-0051).
Until then the UI says nothing about voice except in the plan comparison ("voce: in arrivo, non inclusa").

## 22.10 Notifications  (designed; limits stated)

A web page cannot notify while closed unless **Web Push** is set up with a push service (a third party, plus a
service worker subscription; iOS only for installed web apps and with its own limits - to verify, not assumed here).
**First release: no push.** What the customer sees instead: a badge/list "In attesa di te" when the app is open,
polling (as the panel does, 1.5 s/4 s) and a plain statement "Se chiudi l'app, Jarvis aspetta la tua risposta e non
fa azioni rischiose senza di te". Optional later: email for "decisione in attesa" (open; email is also a data
destination to list). A decision never expires into an automatic "allow": a timeout means deny/idle.

## 22.11 Live view of the hosted desktop/browser  (designed as an OPTION, default off)

Value: the customer can see what the "PC" is doing. Options:

| Option | How | Bandwidth/cost | Privacy |
|---|---|---|---|
| Snapshot on demand / every N s | screenshot image through the API | low, only while viewing | the image can show anything on that desktop, incl. other sessions' passwords typed by Jarvis; blur/pause around credential fields; images not stored by default |
| Stream (noVNC-like or video) | continuous remote display | high egress while open; counts in `egress` and `compute` classes; mobile data of the customer | same plus input channel risk |
| Read-only vs interactive | read-only by default; taking control is a separate, explicit, logged mode | interactive adds an input path that must also respect STOP | |

Rules: **default off**; opt-in per session; read-only first; visible "live view attivo" indicator; auto-close after idle
(cost); counts against the budget (a stream is an accrual); never records without a separate consent; screenshots sent
to a remote model are a separate egress governed by the privacy gate (ADR-0050), not by the viewing consent. Session
credentials for the stream are one-use and not in URLs. Which technology and what it costs: **open** (provider and
measurement).

## 22.12 Accessibility, Italian first  (designed)

Italian strings in a single catalogue (ADR-0063 pattern); English second. Targets at least 44 px; text scales with
the OS setting up to 200% without horizontal scroll; state never by colour alone (icon + text); every control has an
accessible name (`aria-label` in Italian), focus order starts with STOP; live regions (`aria-live="polite"`) for status,
`assertive` only for STOP result and cap reached; honour `prefers-reduced-motion` and dark mode; no timeouts that
remove approvals silently. Screen reader pass on iOS VoiceOver and Android TalkBack is **open** (not tested).

## 22.13 Screen / state / API inventory  (designed)

"Exists" = in the current phone panel or its routes (`phone_routes.py`); "new" = to design/implement for hosting.

| Screen | States | API | Source |
|---|---|---|---|
| Sign-in / create account | new, code sent, wrong code (limited), locked, signed in | `POST /api/cloud/auth/*` (new) | new (replaces PC pairing) |
| Consent | unread, accepted vN, declined | `POST /api/cloud/consent` (new) | new |
| Plan and limits | choose, confirm raise, payment redirect, failed | `GET/POST /api/cloud/plan` (new) | new |
| Environment status | creating, ready, suspended, waking, error, deleting | `GET /api/cloud/env` (new) | new |
| Chat list / chat | idle, sending, waiting engine, waiting approval, stopped, blocked by cap | `/api/phone/chats*` | exists |
| Approval card | pending, stale, mismatch, decided, door closed | `POST /api/phone/chats/{id}/approvals/{aid}` | exists |
| STOP (header) | idle, stopping, partly stopped, stopped, not confirmed | `POST /api/phone/stop`, `GET /api/phone/control-strip` | exists |
| Files | uploading, resumable, ready, expired, over quota | `/api/phone/files*` | exists |
| Consumption | within, near (>= 80%), at cap, over cap (accrual), blocked drift, breaker | `GET /api/cloud/usage` (new, from `BudgetLedger.usage`) | new |
| Memory | browse, correct, export building/ready, delete confirm/progress/partial | `/api/wiki/*` on tenant (exists on desktop), `POST /api/cloud/export`, `POST /api/cloud/delete` (new) | partial |
| Connection bar | connected, slow, offline, env paused, env unreachable | `GET /api/cloud/health` (new) | new |
| Live view (option) | off, starting, viewing read-only, interactive, closing, over budget | `POST /api/cloud/live` (new) | new |
| Devices and sessions | list, revoke | `/api/phone-admin/devices` pattern, hosted variant | new |
| Notifications (later) | off, email decisions | open | open |
| Voice (later) | not shown | open | open |

Every `/api/cloud/*` write: CSRF header, Origin check, tenant from the session never from the body, request id for
effects, rate limit, and a ledger check where it spends. The routes are **not written**; this table is the contract to review.

## 22.14 Reusable frontend (read, not modified)

Static phone panel: `dodaios/ui/web/mobile/{index.html,app.js,order.js,pair.js,sha256.js,sw.js,manifest.webmanifest}` -
chat, approvals, STOP, files, ordering, service worker. Desktop React, as design reference only (no dependency, no edit):
`ControlStrip.tsx` (state strip, four STOP outcomes), `HoldToAbortButton.tsx` (per-task cancel), `AccountUsageMeters.tsx`
and `ProviderBillingBadge.tsx` (usage display patterns), `components/costs/` (`CostTrendPanel.tsx`, `costFormat.ts`),
`onboarding/setup/{SetupWizard,FirstRunSteps}.tsx` (first-run content), `components/memory/*` (`MemoryControl`,
`MemoryHome`, `PageList`, `PageReader`: memory view/correct), `agentchat/{QuestionCard,CredentialCard,AgentComposer}.tsx`
(cards and composer), `ToastLayer.tsx`, `VoiceIndicator.tsx` and `DictationButton.tsx` (only when voice is decided).
No frontend source was edited here: TypeScript was not type-checked, `dist` was not rebuilt, UI NOT live.

## 22.15 Block 22 status table

| Item | State |
|---|---|
| Phone web app / PWA as access | designed (panel exists; hosted login new) |
| Hosted onboarding, consent, plan, no secrets in URLs | designed; legal texts open |
| Connection status | designed |
| Consumption display, measured vs estimated | designed; data source implemented-static |
| Memory location text, export, delete | designed; routes open |
| STOP always visible, per tenant | designed; panel STOP exists (ADR-0062), not run |
| Approvals with request identity | exists (ADR-0062, not run); hosted additions designed |
| Voice only after its path | designed (deferred; off) |
| Limits of phone control | designed (stated text) |
| Live view of hosted desktop | designed as option; technology/cost open |
| Notifications | designed (no push first release); push/email open |
| Accessibility, Italian first | designed; assistive-tech test open |
| Screen/state/API inventory | designed |

## 22.16 Controls prepared, NOT RUN

Hosted login without secrets in URL (history, referrer, server logs); session cookie flags over HTTPS; STOP visible on
every screen and working at cap/suspended/engine-down; approval mismatch/stale/after-STOP on the hosted route; usage
screen shows "non disponibile" not 0 for missing data and keeps measured/estimated apart; cap banner has no automatic
raise; export completeness and delete coverage incl. backups statement; live view default off, auto-close, counted in
`egress`/`compute`; VoiceOver/TalkBack pass; 200% text scale at 360 px; offline message not auto-resent. None executed.

## Decisions needed from Dax

1. Accept the rule "no cap row => nothing is reserved" and "post-hoc accruals may exceed the cap and then block".
2. Who bears a loss when late provider records push a customer past the cap (business rule).
3. Minor unit and currency, and default plan caps (only after provider and prices are known).
4. Idle-suspend delay and the acceptable wake-up delay shown to the customer.
5. Email sign-in method: typed code vs link-with-POST; payment provider (no card data on our pages).
6. Live view: include in the first release at all? If yes, snapshot first, read-only.
7. Any push/email notification channel (each is a new data destination).
8. Provider shortlist must answer the questions in 20.12 before any "maximum you can pay" sentence is written.

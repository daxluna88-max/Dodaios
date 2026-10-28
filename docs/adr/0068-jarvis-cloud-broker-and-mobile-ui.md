# ADR-0068 - Jarvis Cloud: inference BROKER around the budget ledger (block 20) and mobile UI spec (block 22)

**Status:** Proposed. Addendum to ADR-0066 (ledger + first mobile design) and ADR-0065 (isolation).
**NOTHING WAS RUN:** no pytest, no tsc/vitest/npm, no build, no service, no network, no provider call, no
purchase, no account, no model/API call, no download. Only `python3 -m py_compile` and `ruff check` (never
`ruff format`) were executed. No frontend source was edited: nothing type-checked, `dist` not rebuilt, **UI NOT live**.
Date 2026-10-28. Legend: **static** = code/tests written, never run; **designed** = text only; **open** = decision/fact missing.

## 0. Baseline (chosen by Dax/GPT, no purchase)

One OVHcloud VPS-2 per customer, always-on pilot. Inference keys stay OUTSIDE the customer VM, in a node-side broker.
Every AI/voice call: server-side authorization, WORST-CASE reservation, bounded output/tool steps, reconciliation.
Unknown price is not zero. No automatic top-up, no usage overage charged to the customer. The OPERATOR bears any
unreserved/late external billing: stop new work and reconcile. No claim of an absolute provider hard cap unless proven.
Economical default model, explicit escalation allowance (stronger models, computer-use screenshots); stable prompts
cached; retries/concurrency/vision/voice duration capped; stop on quota; no dedicated GPU.

---

# BLOCK 20 - Broker interfaces around `budget_ledger`

## 20.1 Files (all static, NOT RUN)

| File | Role |
|---|---|
| `dodaios/cloud/broker.py` | provider-neutral interface layer: identity, grants, owner price table, worst-case ceiling, authorize/settle/release/late-usage, operator-loss book, holds, reconcile, caps, guest gateway. Pure Python, no network, no SDK, no key. |
| `dodaios/cloud/ledger_budget.py` | adapter: orchestrator `Budget` interface (`reserve/release/reserved/limit`) on top of `BudgetLedger`. |
| `dodaios/cloud/budget_ledger.py` | one additive read-only method `get_entry(res_id)`; nothing else changed. |
| `tests/unit/cloud/test_broker.py`, `test_ledger_budget.py` | written, not executed. |

Nothing imports these modules yet (not wired). The HTTP front door on the node and the provider adapters (which hold
the master keys) are **designed, not written**.

## 20.2 Trust boundary: the guest sees an opaque endpoint and token only

```
 customer VPS (guest, untrusted)            node side (trusted)                 provider
 +---------------------------+   token     +-------------------------+  key   +----------+
 | agent loop                | ---------> | BROKER front door       | -----> | inference|
 | env: BROKER_ENDPOINT,     |  CallSpec   |  GuestCredentialIssuer  |        | voice    |
 |      BROKER_TOKEN only    | <--------- |  Broker -> BudgetLedger |        | vision   |
 | NO provider key, NO route |  Auth/stop  |  OwnerPriceTable        |        +----------+
 | to the provider           |             |  ProviderKeyVault (keys)|
 +---------------------------+             +-------------------------+
```

* `GuestCredential(endpoint, token)` is the only thing handed over; `guest_environment()` builds the complete
  provider-related guest env; `forbidden_env_names()` scans an env for key-like names/prefixes before start (heuristic;
  the real control is that keys never exist on the guest). The broker stores only SHA-256 of tokens; tokens expire and
  are revocable per customer (suspend/delete/device revoke).
* The guest firewall (ADR-0065) must allow egress to the broker endpoint only for inference; a direct provider route
  is closed at the network layer. **Open:** the OVH network rule itself (not verified, not configured).
* The caller never states cost or class limits: the broker derives the ceiling from its own price table and the
  customer from the token, never from the request body.

## 20.3 Cost classes

`CostClass`: inference, voice, vision/screenshots, compute, storage, egress, backup, OPERATOR_LOSS. Mapping to ledger
classes: vision -> `ai_inference`, backup -> `storage`; finer limits (images per call/turn, escalation allowance) are
enforced by the broker. Inference/voice/vision are **calls** (reserve -> settle). Compute/storage/egress/backup are
**accruals** (`record_accrual` from provider usage records; may exceed the cap, then block). `OPERATOR_LOSS` is never
reservable and never attributed to a customer figure.

## 20.4 Price table and unknown price

`OwnerPriceTable`: integer minor units per million input tokens, per million output tokens, per image, per voice
second. Every component defaults to `None` = unknown. `set_price` and `set_call_estimate` need an `actor` and are
audited. `worst_case(spec, price)` = ceil-rounded sum over the components the call needs; any needed component
unknown -> `None`. `None` means: refused (`unknown_price`) unless the owner set an explicit per-call estimate for that
engine/class, then the ledger stores `price_basis=owner_estimate` and the usage view labels it estimated. Cached-input
discounts are NOT assumed in the reservation (worst case = no cache); settle may come out lower. **Open:** the numbers.

## 20.5 Authorization sequence

```
guest            broker                                   ledger
  | CallSpec+token  |                                        |
  |---------------->| resolve token -> Principal             |
  |                 | operator hold? grant? engine tier?     |
  |                 | tokens/images/voice/steps/retries caps |
  |                 | concurrency slot, escalation allowance |
  |                 | worst_case(spec, price) | owner est.   |
  |                 | per-step ceiling                       |
  |                 |--- open_scope(turn) + reserve -------->| BEGIN IMMEDIATE: idem, breaker,
  |                 |                                        | block, cap, class cap, global cap,
  |                 |<-- Decision(ok,res_id | refusal) -----| scope ceiling, insert
  |<-- Authorization(res_id, amount, stop, message_it) ------|
  | (node adapter calls the provider with the VAULT key; bounded max_output_tokens)
  |                 |<-- Usage (measured, node side) --------|
  |                 | settle: customer part = min(actual, reserved)
  |                 |         excess -> OperatorLossBook + HOLD + needs_reconcile
  |                 |--- ledger.settle(part) --------------->|
```

Caps and what refuses them (all with Italian `message_it` and a `StopAction`):

| Control | Code | Action |
|---|---|---|
| bad/expired token | `unauthenticated` | refuse call |
| engine not in grant | `engine_not_allowed` | refuse call |
| escalated engine or vision without allowance / over it | `escalation_not_allowed` | refuse call |
| input/output token bound | `limit_tokens` | stop turn |
| tool/model steps per turn; retries per step; images per call/turn | `limit_steps`, `limit_retries`, `limit_images` | stop turn |
| voice per segment / per session | `limit_voice` | stop turn |
| paid calls in flight per customer | `limit_concurrency` | refuse call (retry later) |
| per-step ceiling (broker) / per-class (ledger) | `call_ceiling` | stop turn |
| per-turn amount and call count (ledger scope, default 0 = fail closed) | `scope_ceiling` | stop turn |
| no cap row, cap/class cap/global cap, customer block, breaker, operator hold | `no_cap`, `cap_reached`, ... | **stop new work** |
| unknown price without owner estimate | `unknown_price` | refuse call |

Every retry is a NEW reservation (the failed attempt may have been billed); a retry reuses the failed attempt's
result only via the same `request_id` (idempotent replay, no double hold). Check-then-register in the broker is atomic
(one lock), so concurrency/step/allowance caps hold under threads; the ledger makes the money check atomic.

## 20.6 Settle, release, late records, operator loss

* `settle(res_id, Usage)`: the customer is booked `min(actual, reserved)`; any excess goes to `OperatorLossBook`
  (pseudo-customer `__operator__` in the same ledger, so it appears in the global reconciliation but in no customer
  view). Loss triggers a per-customer **hold** (`operator_hold`: stop new work) and `needs_reconcile`; optional global
  breaker at a period loss threshold. `clear_hold` needs actor + note. Provider-reported `provider_cost` is preferred;
  otherwise measured tokens x owner price; neither -> `usage=None` -> row stays held as `unknown` (never zero).
* `release(res_id)` only when the provider was provably never contacted (e.g. refused by a later gate).
* `report_late_usage(res_id, total)`: after release/lower settle/expiry: customer figure untouched, difference is
  OPERATOR_LOSS (+ hold). If the row is still `reserved/unknown` it settles instead. `report_unreserved_usage` covers a
  call nobody reserved. Both are idempotent per `(res_id, total)`.
* Customer-visible text never calls loss a debt: `operator_hold` says Jarvis is paused for an accounts check and that
  nothing is asked of the customer. `customer_usage_view` exposes `no_overage=True`, `auto_top_up=False` and no loss.
* Reconciliation: per customer, the provider figure is reduced by loss already attributed to that customer before
  `ledger.reconcile` (loss is not in the customer's rows by design); a block sets the hold. Global reconcile compares
  all rows including `__operator__` and trips the breaker on under-recording.

## 20.7 Failure modes

| Failure | What happens | Residual |
|---|---|---|
| Crash after reserve, before provider call | `recover()` -> `unknown`, still held. Operator `release(operator=True)` only after confirming the call did not run. | Customer capacity stays reduced until resolved. |
| Crash after provider call, before settle | same `unknown` hold; resolved by provider-reported amount (`settle` with `provider_cost`); after restart the engine is unknown in memory so token-only usage stays `unknown` | needs the provider usage feed (open). |
| Double settle | same amount -> `replay`, nothing added (loss is monotonic per res_id); different amount -> `conflict`, no change; a higher later figure must arrive via `report_late_usage` | none |
| Late provider record (after release/settle) | customer untouched; difference = OPERATOR_LOSS, hold, reconcile flag | operator bears it; can lag arbitrarily |
| Unreserved call (bug, bypass, leaked token) | `report_unreserved_usage` -> loss + hold; revoke tokens | detection depends on the provider feed |
| Provider keeps running after cancel / price change | not controllable from here | operator loss; product text promises no absolute ceiling |
| Broker down | `GuestGateway.call` returns `broker_down`, `STOP_TURN`, Italian message; no provider key on the guest, so **the agent cannot call inference at all** | availability tied to the node |
| Ledger DB unavailable/corrupt | `reserve` raises -> authorize fails -> no call (fail closed). Not caught on purpose. | needs a health alarm (open) |
| Process restart | in-flight, step, escalation-used and voice counters reset (in memory); ledger scope `max_calls`/`max_amount` and cap persist | escalation allowance can be re-spent after a restart, bounded by the customer cap (open: persist) |
| Concurrent turns | atomic reservation; ADR-0055 fan-out counts every branch | SQLite single host only |

## 20.8 Orchestrator adapter (`LedgerBudget`)

Duck-type of `consumer_orchestrator.Budget`: `reserve(amount: float|None, allow_unknown)`, `release(amount)`,
`reserved`, `limit`. Float -> integer minor units with **round up** (`scale` per 1.0). `None` + `allow_unknown` still
needs an explicit integer `unknown_estimate` (hosted: unknown is never free). Each reserve uses idem
`<prefix>:<n>`; `release(amount)` frees the most recent open reservation with that many units; unmatched release is
ignored. Settlement is not in `Budget`; the caller uses `settle(res_id, units, measured)`. Orchestrator unchanged. In
the hosted flow the orchestrator's fan-out budget is an inner gate; the broker remains the only path to a key.

## 20.9 Status and controls NOT RUN

Static: broker, adapter, getter, tests (identity/hash/expiry; guest env guard; worst-case maths and unknown; owner
estimate; settle/refund/loss/hold; double settle; unknown usage; release; replay; crash+restart; late and unreserved;
breaker threshold; token/step/retry/concurrency (threaded)/voice/vision caps; escalation; step/turn ceilings; quota
stop messages; reconcile with loss; accruals; customer view; gateway down/malformed). **None executed.**
To catalogue as NOT RUN with a real provider later: OVH egress rule really blocking provider hosts from the guest,
invoice vs ledger drift, provider cap behaviour at the limit, usage-feed latency, SQLite contention.
Open: persist broker counters; price numbers and currency/minor unit; per-customer provider usage tagging;
renewal of long reservations (voice streams, long GUI runs); where `get_entry`-based restart settle gets its engine.

---

# BLOCK 22 - Mobile UI spec (text level; reuses the phone panel, ADR-0062/0066)

Italian first, English second; all targets >= 44 px; state never by colour alone; `aria-live=polite` for status,
`assertive` only for STOP result and quota stop. Voice remains OFF (0066 22.9).

## 22.1 Persistent frame (every screen)

```
+----------------------------------+
| Jarvis Cloud          [ STOP ]   |  STOP: fixed, per customer, always first in focus order
| Collegato - aggiornato 5 s fa    |  connection bar (22.9)
| Jarvis e un'IA. L'umore e una    |  AI-disclosure (dismissible per session, reopenable in Info)
| simulazione, non riconosce le    |
| tue emozioni.                    |
+----------------------------------+
| [Chat][Desktop][File][Lavori]    |  bottom nav; Approvazioni badge on Chat
| [Consumi][Account]               |
```

## 22.2 Screens

**Chat** - reuse panel chat; states idle/sending/waiting engine/waiting approval/stopped/blocked by quota (message
from `message_it`, with "nessuna ricarica automatica").

**Desktop reale (REAL DESKTOP VIEW)** - authenticated live view of the customer's own VPS desktop.
```
+----------------------------------+
| Desktop del tuo PC cloud         |
| [ immagine live ............. ]  |
| Solo visione  (predefinito)      |
| [ Prendi il controllo ]          |  explicit, confirmed, logged; shows "Stai guidando tu"
| Usa dati mobili e consumo rete.  |  privacy/bandwidth note
| Puo mostrare dati privati.       |
| [ Chiudi ]  chiusura auto 2 min  |
+----------------------------------+
```
States: off, starting, viewing view-only, human-in-control, agent paused during takeover, closing, over budget/egress,
unavailable. Rules: view-only default; takeover is a separate authenticated act (re-auth or fresh CSRF + typed
confirm), the agent is paused while the human drives, and STOP and "Restituisci a Jarvis" end it; indicator "live view
attivo" always visible; idle auto-close; stream credential one-use, never in a URL; counts in `egress`/`compute`; no
recording without separate consent; screenshots sent to a model are governed by the privacy gate, not by this consent.

**File** - reuse `/api/phone/files*` (resumable chunks, SHA-256). Download/upload. Each row shows provenance label:
"Caricato dal telefono" (phone-originated; turn label stays UNAVAILABLE/restricted for remote engines, 0062 s.2) vs
"Creato da Jarvis" vs "Caricato da PC". States: uploading, resumable, ready, expired, over quota.

**Lavori** - ONE active job initially.
```
| Lavoro attivo: Riordina cartella |
|  stato: IN ESECUZIONE | IN CODA | BLOCCATO (in attesa di approvazione / limite raggiunto / ambiente in pausa)
|  [ Ferma questo lavoro ]         |
| In coda: 0   (un solo lavoro alla volta)
```
States: queued, running, blocked(+reason), finished, stopped, unknown. Unknown is shown as "non so", never as done.

**Approvazioni** - card shows `request_id`, `tool_name`, `call_fingerprint` (short, expandable), `generation`, target
("nel tuo PC cloud") and risk class; decision echoes all four; mismatch -> 409 `identity_mismatch`, stale -> 409
`stale_generation`; after STOP only "Nega" works; "sempre consenti" absent. A waiting approval never expires into allow.

**STOP** - `POST /api/phone/stop` pattern per customer; outcome per part with the four honest words:
"richiesto / confermato / ancora attivo / sconosciuto"; "tutto fermato" only when all confirmed; text "non annulla cio che
e gia stato fatto". Works at quota, suspended env, broker down. Device revoke is a different button.

**Consumi** - source `customer_usage_view` (never operator loss).
```
| Consumi di novembre                         |
| Misurato 41 | Stimato 3 | In sospeso 2      |  three numbers kept apart; missing = "non disponibile"
| [########------] 62% del limite             |
| Rimane: 380 (unita da definire)             |
| Nessun addebito oltre il limite. Nessuna    |
| ricarica automatica.                        |
| Il conteggio e di Jarvis e puo ritardare    |
| rispetto alla fatturazione reale.           |
| [ Alza il limite ] (conferma esplicita)     |
```
At quota: banner with the ledger message; operator hold shows the neutral hold message, never an amount owed.

**Account** - sessions and devices (list, revoke, "esci da tutti"), consent version, plan/limit, language.
**Dati** - Esporta (readable archive) and Elimina tutto (typed confirm; states: backups remain for N days once known;
"archiviato" is never "eliminato"); both free of budget; progress states in corso/completata/parziale.

## 22.3 Honest states (reuse StripOrderer logic)

Connection/staleness reuses `mobile/order.js` (1:1 port of `controlStrip.ts` `StripOrderer`: boot_id, seq, generation,
barrier, epoch floor) and `STALE_AFTER_MS`. Shown: Collegato / Connessione lenta / Offline / Ambiente in pausa /
Ambiente non raggiungibile / **Dati non aggiornati (stale)** - the age is always printed; stale data is never styled
as live. Queued/blocked jobs state the reason. Undelivered messages say "non inviato - Riprova", never auto-resent.

## 22.4 Screen / state / API inventory

| Screen | States | API | Source |
|---|---|---|---|
| Chat | idle, sending, waiting engine, waiting approval, stopped, quota-blocked | `/api/phone/chats*` | exists |
| Desktop reale | off, starting, view-only, human control, closing, over budget, unavailable | `POST/DELETE /api/cloud/live`, `POST /api/cloud/live/takeover`, stream endpoint | new |
| File | uploading, resumable, ready, expired, over quota | `/api/phone/files*` + `origin` field | exists + provenance label new |
| Lavori | queued, running, blocked(reason), finished, stopped, unknown | `GET /api/cloud/jobs`, `POST .../stop` (mission data = control-strip `MissionJob`) | new route, existing model |
| Approvazioni | pending, stale, mismatch, decided, door closed | `POST /api/phone/chats/{id}/approvals/{aid}` | exists |
| STOP | idle, requested, partial, confirmed, still active, unknown | `POST /api/phone/stop`, `GET /api/phone/control-strip` (per customer) | exists, tenant scope new |
| Consumi | within, near 80%, at cap, hold, breaker, drift block | `GET /api/cloud/usage` -> `Broker.customer_usage_view` | new route, code static |
| Account/sessioni | list, revoke, sign out all | `/api/cloud/auth/*`, `/api/phone-admin/devices` pattern | new |
| Dati | export building/ready, delete confirm/progress/partial | `POST /api/cloud/export`, `POST /api/cloud/delete` | new |
| Connessione | connected, slow, offline, paused, unreachable, stale | `GET /api/cloud/health` | new |
| Banner IA | shown, dismissed | local only | new text |

All `/api/cloud/*` writes: CSRF header, Origin check, tenant from the session never the body, request id, rate limit.
Routes are NOT written.

## 22.5 Component reuse

| Need | Reuse (read, not modified) | New |
|---|---|---|
| Frame, nav, STOP | `mobile/index.html` `#stopall`, `app.js` | bottom nav, per-customer scope |
| Ordering/staleness | `mobile/order.js`, `controlStrip.ts` `StripOrderer`, `isStale`, `viewOf` | - |
| Chat/approvals | `app.js` chat + approval cards, `phone_routes.py` identity check | hosted target/risk line |
| Files | `/api/phone/files*`, `sha256.js` | provenance label |
| Jobs | `controlStrip.ts` `MissionJob`, `ControlStrip.tsx` outcome words | single-job list |
| Usage | `AccountUsageMeters.tsx` patterns, `costFormat.ts` | measured/estimated/pending split |
| Memory/export/delete | `components/memory/*`, 0066 22.4 | export/delete routes |
| Live view | none | stream widget, takeover control |
| Onboarding/accessibility | 0066 22.2, 22.12 | - |

## 22.6 Status

All of block 22 is **designed**; "exists" rows are existing code never run in this task. No frontend source edited; no UI live.

---

## Decisions for Dax (open)
1. Minor unit/currency and the price numbers; escalation allowance size; default limits (all placeholders).
2. Persist broker counters (escalation allowance, steps) in the ledger DB? (recommended: yes before pilot).
3. Hold policy: automatic per-customer stop on any operator loss (as coded) vs only above a threshold.
4. Live view in the pilot: snapshot-only first, or stream with takeover? Cost/egress on OVH unknown.
5. How the OVH VPS network is locked to the broker endpoint, and who runs the provider-key vault.
6. Whether an hard provider-side cap exists for any class (needed before any "maximum" wording).

## Unverified
Everything: no code, test, type-check, build or network rule was executed or tested; the UI is not live.

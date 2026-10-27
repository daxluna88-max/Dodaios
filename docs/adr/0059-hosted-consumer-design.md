# ADR-0059 — Hosted Consumer: reference and what the desktop runtime does not yet assume

**Status:** Proposed (design reference only: no SaaS implementation, no provider chosen, nothing tested) · **Date:** 2026-10-27

## Context

Dax's reference (issue 16, 2026-10-27): about **60 EUR per month total customer spend** to use Jarvis Consumer from a
phone/app or web interface without a PC. This is a design and commercial reference, **not an authorized spend and not
a validated price**. Indicative split, to be evaluated: 10 hosting, memory and backups; 20 AI engines and voice at
usage; 20 operation, support and margin; 10 reserve. VAT, payment fees, support, security and real usage go in a
future profit-and-loss; no margin is guaranteed. Preliminary target: hosting + AI within **25–30 EUR per customer per
month**, to be checked without paid tests.

Consumer and Work stay **two independent products** (ADR-0054). Consumer on the PC and hosted Consumer are two
*distributions* of the same product. Work keeps local-only inference and no cloud fallback (ADR-0049/0053); a hosted
Work offer would be a separate decision, and a private cloud is not "local in the company" nor offline.

## Decision (direction, not implementation)

1. **Shared infrastructure with strict per-customer isolation** for the base plan: data, memory, credentials,
   sessions, tools and jobs. A dedicated installation is a possible higher offer. **No dedicated GPU per customer** in
   the base plan.
2. **Memory lives on the server** and is never described as "on your device". Backup, export and deletion are offered
   with their declared limits (what stays in backups until they rotate).
3. **Privacy gate toward remote engines and in synthesis stays** (ADR-0050): minimum authorized context, categories
   and destinations shown (ADR-0058). Disk encryption does not make data invisible to the operator during processing,
   and the product text must not say so.
4. **Consumption is technical, not advisory:** the customer sees usage and remaining quota in plain words; caps are
   enforced in the server, including under concurrency, multi-engine answers (ADR-0055) and voice; no automatic
   top-up and no automatic overrun; a raise needs an explicit choice. Estimates and unknown prices are not a
   guaranteed monetary ceiling (ADR-0055, D07).
5. **Hosting candidates (from GPT's research; none chosen, no compatibility certified):** Hetzner (EU region) for
   hosting; Scaleway for possible European inference; OVHcloud as an integrated alternative; Infomaniak and Aruba to
   compare. Selection needs data-location, terms-of-service and cost checks that are not done.

## What the desktop runtime assumes today (read in code, not run) and what hosting would change

| Area | Desktop assumption found in code | Needed for hosting (not done) |
|---|---|---|
| Identity and tenancy | one owner per process; per-user Bearer control key (`core/control_key.py`, `ui/web/control_auth.py`); data dir resolved per process (`core/paths.py`, `user_data_dir`) | a tenant id on every path, query and job; one namespace per customer; no process-wide singletons for state |
| Authentication | local UI session / loopback; `assert_bind_safe` already fails closed for non-loopback binds without a key | customer accounts, sessions, recovery, rate limits, device pairing; the phone companion's pairing (Dodaios #4) is local-network oriented |
| Credentials | OS keyring service per product (`dodaios-consumer`) | a server-side secret store per tenant; the customer's own keys (if allowed) never shared; no OS keyring on a server |
| Storage | files under the data dir, SQLite stores (board, agentic IDE, memory wiki) | tenant-scoped storage, backups, export, deletion across files, indexes and caches; concurrency limits of SQLite |
| Process model | one UI/server process, port 47821 (+ product offsets), launch lock files | multi-tenant service or one sandboxed worker per active tenant; no fixed port; no per-machine lock |
| Tools | tools act on the local computer (files, browser, apps, computer use, screen) | most are meaningless or dangerous on a shared server; a hosted tool allow-list, sandboxed, no shell access to the host; local-computer tools only through an explicit paired device |
| Voice | microphone/speakers of the PC, local STT/TTS options | streaming audio from app/web, server-side STT/TTS cost metering, echo/latency budget |
| Engines | local engines (Ollama etc.) and the customer's own accounts | the operator's engines and metering; a local model is not available per customer; subscription logins (Claude/Codex style) are not API credit and not transferable |
| Cost control | `[brain.multi_engine]` budget, costs module | a per-tenant, server-enforced ledger with hard caps, an audit trail, and plain-language usage |
| Updates and operations | auto-update of the desktop app | deployments, migrations, monitoring, incident handling, support access with the customer's consent and an audit log |
| Compliance | none in code | privacy notice, data location, retention, deletion on request, subprocessor list, backups' retention |

None of this is built. Deploying today's desktop runtime as a service would share state between customers and run
local-computer tools on the operator's server: it must **not** be done.

## Not decided here / limits

- No SaaS implementation is started from this reference. No provider, price, plan, legal form or margin is decided.
- The 25–30 EUR target is a hypothesis; it depends on real per-customer usage, which has not been measured.
- Whether subscriptions or the customer's own API keys may power a hosted instance is a terms-of-service question
  (issue 16, D08) that is not answered.
- The phone companion and the hosted offer overlap (access from a phone); how they relate is open.
- Controls for a future hosted deployment (tenant isolation, per-customer caps under concurrency, deletion
  completeness, backups) are to be catalogued in issue 16 as NOT RUN when the design is agreed.

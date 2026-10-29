# Jarvis web app, creator marketplace and referrals

Status: product direction approved by Dax on 2026-10-29. Begin local audit
and preparation of the first phase. Publication, payment services and launch
remain outside this approval; implementation follows the approved phase order.
This extends the Jarvis product plan without closing R1–R8 or blocks 5–7.

## Product goal

Give owners a pleasant Italian web app, let the community build useful
extensions and earn from them, and reward sustainable customer acquisition.
The current mobile sidebar is the redesign trigger: too many technical
destinations and English labels obscure everyday actions.

## 1. Everyday web app

- Responsive, installable PWA for iPhone, Android and desktop; Italian first.
- Primary navigation: Home, Chat, My Jarvis, Market. Put technical settings
  and developer tools behind an advanced area.
- Visible conversation history, reliable session reopening after reload,
  clear connection/reconnection state and onboarding with guided steps.
- Treat the PWA as the interface to the owner's Jarvis instance. Installation
  must not silently move local memory or model inference into a cloud service.
- Secure device pairing and revocation; do not expose an unauthenticated
  terminal or ask ordinary users to manage SSH tunnels for daily use.

Acceptance: on an actual iPhone, an owner can pair, send a message, reopen
an existing chat, reload, reconnect and change settings without a terminal.
Saved UI, reply and recognition languages remain independent.

## 2. Creator studio and agent terminal

An advanced workspace lets an owner describe an extension to an agent, inspect
its proposed changes, preview it and package it. A Termius-like terminal is
an optional expert surface, not the ordinary home screen or a required vendor.
No terminal vendor is selected by this proposal.

Run agent commands in an isolated project workspace with scoped filesystem
and network permissions. Show commands, changes and execution logs; provide
stop/cancel, time/resource limits and rollback. Privileged, destructive and
paid operations require explicit approval. Do not expose host credentials to
generated applications or inherit unrestricted shell access from a chat.

Start with declarative skills, workflows, agent templates and themes supported
by the existing package model. Arbitrary executable mini-apps require a
separate sandbox and permission design before they become installable.

## 3. Community market and creator earnings

Audit and reuse the existing marketplace rather than building a second registry.
Existing reference documents include `docs/marketplace/community-registry.md`,
`publishing-plan.md` and `agent-plugins-standard.md`. Their upstream claims of
shipped/live behavior are not evidence of availability in Dax's installation.
Upstream registry/storefront repositories are not automatically Jarvis-owned.

Target flow: create -> preview -> package -> validate/review -> publish ->
discover -> inspect permissions -> install -> update or roll back -> uninstall.
Each listing identifies its publisher, exact version, permissions, compatible
Jarvis product, price/license and support channel. Updates that expand
permissions require renewed consent. Include reporting and revocation.

Creators can offer free and paid extensions. A creator dashboard shows gross
sales, deductions, refunds, pending balance and settled earnings separately.
Revenue share, prices, payout thresholds and payment provider remain proposals
to validate against actual costs; no earnings guarantee or percentages are set.
Payment, tax and seller terms must be settled before paid marketplace launch.

PMI remains an independent local-only product. Installing a consumer extension
must never bypass its offline policy, enable a cloud fallback or share memory.
Market discovery/download, if permitted, is an explicit provisioning operation
outside offline inference; offline package import must remain possible.

## 4. Single-level referrals

Give each participating owner a referral link/code and a transparent dashboard.
Track invitation, qualified activation, eligible payment, pending reward,
settlement and reversal as separate events. A download alone does not create
a cash reward: it is too easy to fake and creates an unfunded liability.

Recommended starting model: a capped credit for a qualified activation, or a
share of an eligible collected payment after its refund window. Choose the
model only once Jarvis pricing and contribution margin are known. If Jarvis
downloads are free, identify a funding source before promising cash rewards.

Use one referral level, prohibit self-referrals and duplicate attribution,
apply reward caps and fraud review, and reverse rewards for refunded purchases.
Use unique event/payment identifiers and an auditable ledger so retries cannot
pay twice. Publish attribution duration, payout eligibility and program terms.
Do not pay people merely for recruiting further recruiters.

## Delivery order and evidence

1. Audit existing UI/marketplace behavior and map reusable components. Resolve
   current navigation/history defects; design and approve the new mobile UI.
2. Implement and validate the everyday PWA against the iPhone acceptance flow.
3. Add a free community catalog and guided creator packaging with permissions.
4. Pilot creator sales with a bounded group after billing/refund/payout design.
5. Pilot referrals after retention and unit economics justify a reward budget.

Measure successful onboarding, return usage, extension activation, refunds,
support cost and contribution margin per referred customer. Growth is a
hypothesis to test, not a promised viral outcome. Avoid launching a broad
market before Jarvis is reliable enough for owners to recommend it.

This document adds requirements only. Existing restrictions on pushes beyond
the approved source delivery, PRs, merges, Actions, builds, deployment and
spending remain in effect. No external notification is implied.

## Phase 1: initial source inventory

Static inspection only; no claims about the deployed application:

| Area | Existing source | Next bounded task |
|---|---|---|
| Responsive shell | `frontend/src/App.tsx`, mobile `nav-sheet` | Map four primary destinations onto existing routes; preserve mobile fixes during review. |
| Navigation and recent chats | `frontend/src/components/layout/Sidebar.tsx` | Separate everyday navigation from advanced tools without hiding existing conversations. |
| Community catalog | `frontend/src/views/PluginsCommunity.tsx` | Verify current index ownership, availability and install consent before rebranding. |
| Creator publishing | `frontend/src/components/marketplace/PublishStudio.tsx` | Inspect draft, validation and submission stages; adapt this flow rather than duplicate it. |
| Marketplace API | `dodaios/ui/web/marketplace_routes.py`, `marketplace_publish_routes.py` | Audit auth, permissions, provenance and external submission destinations. |

Frontend paths above are relative to `dodaios/ui/web/`. Next design deliverable:
a mobile navigation specification for Home / Chat / My Jarvis / Market, with
existing technical destinations retained under Advanced. Review the visual
reference list before writing website/UI code, as requested by Dax. Do not
infer a PWA installation/offline capability from a responsive web UI alone.

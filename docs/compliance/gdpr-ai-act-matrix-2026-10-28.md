# Jarvis Cloud (hosted Consumer): GDPR and EU AI Act traceability matrix

Date: 2026-10-28 (all web sources accessed 2026-10-28) - Branch `claude/cloud2-e` - Status: **DRAFT FOR A PROFESSIONAL; design-only analysis**

> **I am not a lawyer and this is not legal advice.** Every legal conclusion below is **to be confirmed by a qualified
> privacy/AI-law professional**. Nothing was run, bought, registered or sent; no provider, authority or lawyer was
> contacted. Only public official pages were read. Section 9 states what this document is NOT.

Owner requirement (Dax): the hosted Jarvis (Consumer cloud) MUST respect EU law, in particular the GDPR and the EU AI Act,
as a **release requirement**; low cost must not weaken it. This file turns that into a checkable matrix. It does not
claim that any of it is satisfied: the "evidence" column is **NONE** unless a file exists, and an ADR describing a control
is a design statement, not evidence that the control works.

## 0. Baseline architecture assumed (design only, nothing chosen or bought)

One OVHcloud VPS per customer (always-on pilot); Ubuntu + X11/Xvfb + Chromium/Playwright + persistent profile; authenticated
noVNC live view with human takeover; node-side broker holding inference keys (ADR-0065 s.10, open decision 3);
off-instance encrypted backups (ADR-0065 s.11.2); AI and voice through external model endpoints (vendor TBD).
Repo inputs read: ADR-0050, 0056, 0058, 0059, 0061, 0062, 0065, 0066; `docs/research/ai-costs-2026-10-28.md`;
`docs/research/cloud-decision-sheet-2026-10-28.md`; `docs/research/cloud-providers-vm-2026-10-28.md`; `docs/legal/TERMS.md`;
`docs/telephony.md`. **ADR-0067 does not exist in this worktree or in any local branch** (searched); the Art 32 mapping
therefore uses ADR-0065/0066 only and must be re-mapped if 0067 appears.

## 1. Official sources used (URL + access date) and the legal timeline found

| Key | Source | URL | Accessed |
|---|---|---|---|
| GDPR | Regulation (EU) 2016/679, EUR-Lex | https://eur-lex.europa.eu/eli/reg/2016/679/oj | 2026-10-28 (article numbers cited from knowledge of the text; the fetch tool returned no page body, so **article wording not re-read here: verify**) |
| AIA | Regulation (EU) 2024/1689 (AI Act), EUR-Lex; consolidated version 27.07.2026 | https://eur-lex.europa.eu/eli/reg/2024/1689/oj ; https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX%3A02024R1689-20260727 | 2026-10-28 (consolidated PDF found by search; article text read through the Commission service desk, below) |
| OMN | Regulation (EU) 2026/1744 ("Digital Omnibus on AI"), dated 8 July 2026, published OJ 24 July 2026, amends 2024/1689, 2018/1139, 2023/1230 | https://eur-lex.europa.eu/eli/reg/2026/1744/oj/eng | 2026-10-28 (page body not returned by fetch; metadata from search result of EUR-Lex pages; the date of effect 27 July 2026 comes from the EUR-Lex record as summarised by search: **verify**) |
| SD | Commission AI Act Service Desk (article pages, "based on EUR-Lex consolidated version as at 27 July 2026", marked non-binding) | https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-4 ; /article-5 ; /article-50 ; /article-113 ; /annex-3 | 2026-10-28 (read) |
| SD-TL | Commission AI Act Service Desk timeline | https://ai-act-service-desk.ec.europa.eu/en/ai-act/timeline/timeline-implementation-eu-ai-act | 2026-10-28 (read) |
| L132 | Italian Law 23 Sept 2025 n. 132 (AI), GU Serie Generale n. 223 of 25-09-2025, in force 10 Oct 2025 | https://www.gazzettaufficiale.it/eli/gu/2025/09/25/223/sg/pdf (text of Art 4 and Art 20 read from the PDF) | 2026-10-28 (read) |
| GAR-DPIA | Garante provv. n. 467 of 11 Oct 2018 (GU n. 269 of 19 Nov 2018), doc. web 9058979, Allegato 1 (12-item DPIA list) | https://www.garanteprivacy.it/home/docweb/-/docweb-display/docweb/9058979 ; https://www.garanteprivacy.it/web/guest/home/docweb/-/docweb-display/docweb/9059358 | 2026-10-28 (Allegato 1 PDF read in full) |
| COP50 | Commission press release on the final Code of Practice on marking and labelling AI-generated content (10 June 2026) and Art 50 guidelines (20 July 2026) | https://ec.europa.eu/commission/presscorner/detail/en/ip_26_1328 ; https://digital-strategy.ec.europa.eu/en/news/commission-publishes-code-practice-marking-and-labelling-ai-generated-content | 2026-10-28 (found by search; dates of the guidelines come from a **secondary** law-firm note: secondary, verify) |
| DPF-CASE | CJEU InfoCuria, case C-703/25 P (Latombe appeal of General Court T-553/23) | https://infocuria.curia.europa.eu/tabs/affair?lang=EN&searchTerm=%22T-553%2F23%22&publishedId=C-703%2F25+P | 2026-10-28 (search result only; status "pending" from secondary summaries: secondary, verify) |
| OVH-DPA | OVHcloud DPA and EU privacy notice (vendor documents, not law) | https://us.ovhcloud.com/legal/data-processing-agreement/ ; https://us.ovhcloud.com/legal/eu-privacy-notice/ | 2026-10-28 (search summary only; the EU contracting entity's DPA, sub-processor Annex III and support locations NOT verified) |

### 1.1 AI Act application dates found (state at 2026-10-28; do NOT freeze)

From the Commission service desk timeline (SD-TL), which marks with `**` the dates changed by the Digital Omnibus on AI:

| Date | What applies | Source |
|---|---|---|
| 1 Aug 2024 | entry into force | SD-TL |
| 2 Feb 2025 | definitions, prohibitions (Art 5), AI literacy (Art 4) | SD-TL |
| 2 Aug 2025 | general-purpose AI model rules, governance, penalties | SD-TL |
| **2 Aug 2026** | "majority of rules", **Art 50 transparency**, enforcement of GPAI, prohibitions, transparency, AI literacy | SD-TL |
| **2 Dec 2026** (amended) | new prohibitions Art 5(1)(ba),(bb) (non-consensual intimate/sexual deepfakes, CSAM); **Art 50(2) marking transition only for systems placed on the market before 2 Aug 2026** | SD-TL, SD art-5, SD art-113 |
| 2 Aug 2027 | one AI regulatory sandbox per Member State | SD-TL |
| **2 Dec 2027** (amended) | high-risk rules for **Annex III** systems (Art 6(2)) | SD-TL, SD art-113 |
| **2 Aug 2028** (amended) | high-risk AI embedded in Annex I products (Art 6(1)) | SD-TL, SD art-113 |

Amending act: Regulation (EU) 2026/1744 (OMN). Secondary sources (law-firm notes) report: provisional deal 7 May 2026,
Parliament endorsement 16 June 2026, Council adoption 29 June 2026: **secondary, verify**. **Art 4 (AI literacy) was
amended**: the service desk shows all three paragraphs marked AMENDED; para 1 now reads as an obligation on providers and
deployers "to take measures to support AI literacy", expressly **not** requiring any specific level for any individual.
**Art 50 paragraphs 1-5 carry no amendment label** (text read in SD art-50). Consequence for Jarvis Cloud: because the hosted
service would be placed on the market after 2 Aug 2026, **the Art 50 duties apply from day one and the Art 50(2) transition
does not help it** (to be confirmed by a professional). High-risk (Annex III) timing is irrelevant to the initial offering
only if the offering stays out of Annex III uses (section 4); it is not a reason to skip the restriction work.
Uncertainty: the omnibus is recent; delegated/implementing acts, Commission guidelines (including on high-risk
classification and on Art 50) and national laws may still move. Re-check these pages at every gate in section 8.

### 1.2 Italian points found

- **Age**: L132 Art 4(4) (read in the GU PDF): access to AI technologies by persons **under 14** and the related processing of
  personal data "require the consent of whoever exercises parental responsibility"; a minor over 14 may consent to the
  processing connected with AI use provided the information under Art 4(3) is easily accessible and understandable.
  Art 4(3): processing information for AI use must be in clear, simple language so the user knows the risks and the right
  to object. Separate GDPR Art 8 / Codice privacy Art 2-quinquies (14 years for information-society services) was found only
  in **secondary** sources: secondary, verify on Normattiva.
- **AI authorities**: L132 Art 20(1) designates AgID and ACN as national AI authorities (market-surveillance roles for financial
  supervisors remain with Banca d'Italia, CONSOB, IVASS). Art 20(4) leaves the Garante's powers unchanged.
- **DPIA list**: Garante Allegato 1 has 12 items. Directly relevant (read): item 1 (scoring/profiling/predictive, including via
  app), 3 (systematic monitoring incl. via app), 4 (large scale, extremely personal data incl. communications and financial
  data), 6 (non-occasional processing of vulnerable subjects incl. minors), **7 (innovative technologies, expressly "AI systems;
  online voice assistants through voice and text scanning", whenever at least one further WP248 criterion applies)**,
  9 (combining datasets), 10 (Art 9/Art 10 data interconnected with other data).

## 2. Processing map of the ACTUAL flows (analysis, to be confirmed)

Roles are provisional. Key problem: the customer is a natural person acting privately, but **the household exemption (GDPR
Art 2(2)(c), Recital 18) covers the person, not the operator** who provides the means, and Recital 18 says controllers or
processors providing the means for personal activities remain covered. Where the customer is purely private there may be **no
controller above the operator**, which makes the operator the likely controller (not a processor) for most flows below. Where
the customer uses it professionally (sole trader, firm), the customer is likely controller and the operator processor, needing
an Art 28 DPA. The business model must choose (open question Q1). Third-party personal data in files, mail and pages
(contacts, correspondents, people on visited pages) concerns data subjects who have no relationship with the operator.

| # | Flow / data | Where it lives or goes | Purpose | Lawful basis candidates (to confirm) | Role (provisional) | Special categories / Art 10 | Retention / deletion (design) |
|---|---|---|---|---|---|---|---|
| F1 | Account and contact data (name/email, auth, devices, passkeys, recovery) | control plane DB (ADR-0065 s.4-6) | provide service, security | Art 6(1)(b); 6(1)(f) security | operator controller | no | life of account; then deletion except legal (ADR-0065 s.9-bis) |
| F2 | Payment and invoicing data | PSP (TBD) and operator books | billing, tax | 6(1)(b); 6(1)(c) tax | PSP often independent controller; operator controller of invoices | no | statutory retention (to confirm) |
| F3 | Transactional email / notifications (provider TBD) | email vendor | login, alerts | 6(1)(b) | email vendor = processor | no | vendor logs unknown |
| F4 | Customer files and the "PC" (`home/`, uploads, outputs) | customer volume on OVH VPS (ADR-0065 s.3.5) | provide the PC | 6(1)(b) | operator controller or processor (Q1); OVH sub-processor/processor | **can contain Art 9/10 data of customer and third parties** | until deletion/crypto-erase; backups per schedule |
| F5 | Browser profile: cookies, saved logins, session tokens, history, caches (`society/<agent>/browser-profile`) | customer volume; live view | act as the customer on websites | 6(1)(b) | as F4 | credentials to health/bank/other sites; page content may be Art 9 | as F4; cookies not portable (ADR-0065 s.9-bis) |
| F6 | Prompts, turn context, screenshots (computer use screenshots are billed as image input) sent to AI vendors | AI endpoints via node broker | generate answers and actions | 6(1)(b); special categories: see Q3 | AI vendor = processor or independent controller depending on terms (verify); privacy gate ADR-0050 minimises | yes, incidental | vendor retention (section 3.1) |
| F7 | Memory/notes/learning (USER.md, MEMORY.md, wiki, proposals) | customer volume | personalisation | 6(1)(b); learning/analysis beyond request: consider consent 6(1)(a) | as F4 | possible | ADR-0061 delete report; 30-day logical trash; permanent mode |
| F8 | Content logs, turn records | customer volume (ADR-0065 s.11.4) | support/debug by the customer | 6(1)(b) | as F4 | possible | exported/deleted with the volume |
| F9 | Operator logs (metadata only: customer_id, event codes, bytes, destination host) | operator log store | security, abuse, billing | 6(1)(f) | operator controller | no (by design) | short fixed retention (value TBD) |
| F10 | Backups/snapshots (off-instance, encrypted per customer) | off-VPS store (provider TBD) | restore | 6(1)(b),(f) | as F4 | contain all of F4-F8 | retention TBD; expiry must be stated (Art 17) |
| F11 | Audio/voice (browser mic to STT, TTS back; voice minutes metered) | STT/TTS vendors TBD | voice assistant | 6(1)(b) or consent; voice of third parties in the room | vendor processor (verify) | voice is not Art 9 unless used for unique identification: **do not do speaker ID** | vendor retention NOT FOUND for most |
| F12 | Telemetry/metrics (internal, over private channel), ledger usage rows (BudgetLedger) | control plane | billing, capacity | 6(1)(b),(f) | operator controller | no | TBD |
| F13 | Support/remote access (break-glass, "allow support to view for 30 minutes") | operator staff view of logs/screen/files | support, incident | consent per case 6(1)(a) or 6(1)(f); legal exception | operator controller | possible | audit entry visible to customer (ADR-0065 s.7) |
| F14 | Live-view sessions (noVNC gateway, human takeover) | gateway, stream tokens | customer sees/controls the screen | 6(1)(b) | operator | screen content | no recording by default (must stay default; any recording needs its own basis) |
| F15 | OAuth token exchange via project service `token.personaljarvis.ai` on Cloudflare (TERMS.md item 5) | Cloudflare | Google/Slack sign-in | 6(1)(b) | operator controller; Cloudflare processor | no | **server-side logging/retention not guaranteed per TERMS.md**: gap |
| F16 | Outbound actions to third parties (emails, messages, calls; optional Twilio telephony, `docs/telephony.md`) | recipients, Twilio if enabled | act for the customer | customer-driven; operator role unclear | third parties are data subjects | possible | Twilio off in base plan (decision) |
| F17 | Abuse/suspension data (egress signals, complaints) | operator | stop abuse | 6(1)(f) | operator controller | no | stated period (TBD); Art 22 note in M-rows |

## 3. Vendor facts already in the repo (NOT verified by me; from `ai-costs-2026-10-28.md`, which read vendor pages via a summarising tool)

### 3.1 AI vendors (retention, training, region, DPA)

| Vendor | Facts recorded in repo | Gaps (NOT FOUND in repo) |
|---|---|---|
| Anthropic | API inputs/outputs deleted within 30 days; Commercial Terms: no training on Customer Content; DPA incorporated; ZDR by agreement only; `inference_geo` supports only `global` and `us`: **no EU-only first-party inference documented**; Bedrock/Google-managed regional endpoints cost +10% (EU list NOT FOUND); Commercial Terms D.4 forbid resale unless approved, A.1 allows powering products for the customer's own users | EU residency; sub-processor/support-access locations; SCC/DPF reliance |
| OpenAI | API data not used for training unless opted in; abuse-monitoring logs up to 30 days; ZDR by approval; EU (EEA + CH) residency via `eu.api.openai.com` per project after sales approval (+10%) | DPA terms NOT FOUND; support-access locations |
| Google Gemini | Free tier: content used to improve products, human review: **unusable**; paid required to serve EEA/CH/UK; paid: logs for abuse detection, data "may be stored transiently or cached in any country" | region control; DPA |
| Mistral (EU vendor) | priced | API training policy, data location, DPA: NOT FOUND |
| DeepSeek, xAI, Cohere, Alibaba, Groq, Together, Fireworks, DeepInfra, OpenRouter, Moonshot | priced only | retention/training/DPA/region NOT FOUND; legal entity and location of DeepSeek unverified: **do not route EU customer data until resolved** |
| OVHcloud AI Endpoints / Scaleway / Infomaniak / IONOS | priced | hosting country, retention, DPA NOT FOUND (Scaleway official pages 403) |

**no training != no retention** (30-day deletion windows and abuse-monitoring logs are retention). EU hosting of the VPS does not
show where prompts go: a US-parent AI vendor or a `global` inference geo implies a Chapter V analysis (rows C-1..C-4).

### 3.2 OVHcloud (hosting)

Repo: VPS-1 priced; Italian data centre NOT FOUND; locations FR/DE/PL/UK/CA etc.; billing/DPA not read. Search summary of OVH
documents (secondary to the documents themselves): DPA exists; sub-processor list sits in Annex III of the SCCs; customers have
15 business days to object to sub-processor changes; support teams may be in the EU **and Canada** (adequacy decision for Canada
is limited to commercial organisations subject to PIPEDA: to be confirmed); the notice mentions DPF for onward transfers. Which
OVH entity contracts with an Italian operator, on which VPS product terms, and whether any non-EEA support access exists for the
chosen product are **unverified**. Do not claim "data stays in the EU".

## 4. Traceability matrix

Release decision key (used consistently):
- **BLOCKER** = must be true before **any real customer personal data** enters the system (staff-only, synthetic data allowed before).
- **before-pilot** = before inviting external pilot customers (can follow BLOCKERs, must precede the pilot).
- **before-public** = before general availability / marketing.
- **n.a.** = not applicable to the initial offering as designed (with the condition that makes it n.a.).

Evidence column: "NONE" means no document, test, signed contract or record exists in the repo. "Design text" means an ADR
describes intent; that is not evidence of operation. Nothing was run anywhere.

### 4.1 GDPR

| ID | Requirement | Legal source | Responsible party | Implementation (code/ADR/control) | Evidence available | Open gap | Release decision |
|---|---|---|---|---|---|---|---|
| G-01 | Determine roles per activity (controller / processor / joint); household exemption does not cover the operator | GDPR Art 4(7),(8), Art 2(2)(c), Recital 18, Art 24, 26, 28 | Operator (with counsel) | ADR-0065 s.9-bis (analysis only: customer "typically" controller, operator processor) - **this analysis is probably too simple for private users** | NONE | Q1: B2C-only vs mixed; role per flow F1-F17 | BLOCKER |
| G-02 | Lawful basis per purpose (Art 6) documented | GDPR Art 5(1)(a), 6 | Operator | Section 2 candidates | NONE | LIA for security/abuse; consent design for optional features | BLOCKER |
| G-03 | Special categories may appear in files, pages, prompts, voice: Art 9(2) condition or avoid | GDPR Art 9(1),(2) | Operator (and customer for own data) | Design intent only: privacy gate ADR-0050 minimises context to engines; ToS to discourage | NONE | no Art 9(2) condition identified for third parties' special data in files; explicit consent covers the customer only; Q3 | BLOCKER |
| G-04 | Criminal-conviction data (Art 10) | GDPR Art 10 | Operator | none | NONE | same as G-03 | BLOCKER |
| G-05 | Controller-processor contract chain: operator<->OVH | GDPR Art 28(1),(3),(4) | Operator, OVH | none (OVH DPA exists publicly: OVH-DPA, unverified) | NONE (no signed/accepted DPA, no review) | confirm contracting entity, DPA version, Annex III sub-processors, audit rights, deletion at exit, support-access | BLOCKER |
| G-06 | Contract chain: operator<->each AI vendor in the chosen set | Art 28, 29, 32 | Operator, AI vendors | Vendor facts section 3.1 only | NONE | per vendor: DPA, sub-processors, retention, training, region, role (processor vs controller); only vendors with a completed review may be enabled | BLOCKER |
| G-07 | Contract chain: payment provider, email provider, STT/TTS, monitoring/logging, backup storage, Cloudflare OAuth service | Art 28, 26 (if joint) | Operator, each vendor | none | NONE | list of sub-processors does not exist yet | BLOCKER |
| G-08 | Sub-processor list published/notified; change-notice mechanism | Art 28(2),(4) | Operator | none | NONE | build list from G-05..G-07 | before-pilot |
| G-09 | Customer-facing contract: for business users a DPA (if operator is processor); for consumers terms + privacy notice | Art 28(3), 13, 14 | Operator | `docs/legal/TERMS.md` is for the free open-source desktop app and says the opposite of a hosted terms (e.g. "runs on your computer", "no liability") | `docs/legal/TERMS.md` (not applicable to hosted) | new hosted ToS/AUP, privacy notice, DPA template | BLOCKER |
| G-10 | Records of processing activities (controller and processor versions); Art 30(5) exemption unlikely (not occasional, may include Art 9) | Art 30 | Operator | Section 2 is a starting draft | Draft only (this file); no formal record | convert to RoPA, keep updated | BLOCKER |
| G-11 | Privacy notice at collection (Art 13) and for third-party data obtained indirectly (Art 14), clear language; Italian AI-specific clarity | Art 12(1), 13, 14; L132 Art 4(3) | Operator | none; ADR-0059 s.8 lists as out of scope | NONE | notice text in Italian; Art 14 notice to third parties likely impossible/disproportionate (Art 14(5)(b)): needs a documented position | BLOCKER |
| G-12 | Plain statement that memory lives on the server, encryption does not hide data from the operator, no "only you can read" claim | Art 5(1)(a), 12, 13 | Operator | ADR-0059 s.4; ADR-0065 s.8 (design text) | Design text | enforce in UI copy and ToS review | before-pilot |
| G-13 | Right of access (Art 15): copy of data incl. account, ledger, logs, support-access audit | Art 12(3) one month, Art 15 | Operator | export archive in ADR-0065 s.9-bis (design); audit log visible to customer s.7 (design) | NONE | workflow, identity check (Art 12(6)), what is in/out of scope (operator logs keyed by customer_id) | before-pilot |
| G-14 | Portability (Art 20): structured, commonly used, machine-readable | Art 20 | Operator | export archive + manifest (design) | NONE | scope covers data "provided by" the subject; browser profile/logins not portable (stated limit) | before-pilot |
| G-15 | Rectification (Art 16) | Art 16 | Operator | ADR-0061 memory edit/approve/reject (code written, not run) | Code written, not run | cover non-memory stores; third-party vendors' copies | before-pilot |
| G-16 | Erasure (Art 17) incl. backups expiry and vendor copies; crypto-erase | Art 17, 19 | Operator, OVH, backup store, AI vendors | ADR-0065 s.9-bis: destroy DEK, delete volume/snapshots; ADR-0061 deletion report with residual list | Design text; tests NOT RUN | state backup retention window to the customer; vendor retention (30 days etc.); Art 19 notify recipients; deletion completeness test not run | BLOCKER |
| G-17 | Restriction (Art 18) and objection (Art 21) | Art 18, 21 | Operator | STOP / suspend / sleep (ADR-0058, ADR-0065 s.12) | Design text | define a "restricted" state in which data is kept but not processed; objection to 6(1)(f) processing | before-public |
| G-18 | Rights requests intake, deadlines, logging, refusal handling | Art 12 | Operator | none | NONE | mailbox/process, template answers, request register | before-pilot |
| G-19 | Rights of third parties whose data sit in customer files | Art 14, 15-21 | Operator/customer (unclear) | none | NONE | who answers; Q1/Q3 | before-public |
| G-20 | Automated decision-making: Art 22 applies to decisions with legal/similar effects on the data subject; Jarvis acts as the customer's tool; operator-side automated suspension/abuse decisions could qualify | Art 22, 13(2)(f), 15(1)(h) | Operator | graded suspension with appeal channel (ADR-0065 s.11.1) design text | Design text | human review step for suspensions; AUP forbids use of the agent for decisions on third parties in Annex III areas (see AIA rows) | before-pilot |
| G-21 | Data protection by design: privacy gate, minimum authorised context, `local_only` never leaves, derived items inherit, masked identifiers | Art 25(1) | Operator | ADR-0050 (library); ADR-0062 provenance fail-closed for phone attachments; code written, tests NOT RUN | Code + tests written, not run | redaction is "a floor, not anonymization"; screenshots in computer use bypass text redaction | before-pilot |
| G-22 | Data protection by default: learning off until turned on, approvals per note, no operator access by default, content-free operator logs, no recording | Art 25(2) | Operator | ADR-0057/0061 (learning off, per-note approval); ADR-0065 s.7, 11.4 | Design/code not run | defaults must be tested in the hosted image | before-pilot |
| G-23 | Security of processing (appropriate technical/organisational measures; risk-based) | Art 32 | Operator, OVH, vendors | ADR-0065: VM-per-customer boundary, per-customer envelope encryption, egress default-deny outside the guest, no SSH/secrets in image, key broker outside guest, break-glass JIT, supply chain; ADR-0066 budget gate. **ADR-0067 not present**. The single-VPS baseline with VPS-level isolation is weaker than a microVM (T1 in ADR-0065) | Design only; none of the controls in ADR-0065 s.14 was run | pen-test / control tests not run; confidential computing not assumed; node firewall capabilities of OVH VPS NOT verified (Appendix A items "outside-guest firewall" "metadata policy") | BLOCKER |
| G-24 | Operator insider access and break-glass, audit visible to customer | Art 32(4), 29 | Operator | ADR-0065 s.7 | Design text | tooling not built | BLOCKER |
| G-25 | Backups: encrypted, off-instance, verified restore, retention stated | Art 32(1)(c), 17 | Operator, backup store | ADR-0065 s.11.2; `core/product_backup.py` (desktop, not run) | NONE | backup store not chosen (an Art 28 vendor), restore drills not run | BLOCKER |
| G-26 | Breach detection and notification: 72 h to the supervisory authority (Garante), processor-to-controller without undue delay, communication to individuals if high risk, internal breach register | Art 33, 34, 33(5) | Operator; OVH/vendors notify operator | ADR-0065 s.11.5 "open, to write" | NONE | runbook, contacts, templates, vendor notice clauses, rehearsal; Garante notification channel to be confirmed on garanteprivacy.it | BLOCKER |
| G-27 | DPIA required? Screening against WP248 rev.01 criteria and the Garante list | Art 35(1),(3),(4), Garante Allegato 1 | Operator | Screening below (4.3) | This file (screening only) | full DPIA not done; Art 36 prior consultation if residual high risk | BLOCKER |
| G-28 | DPO: mandatory only in Art 37(1) cases (public body; core activity = regular and systematic monitoring on large scale; core activity = large-scale special categories/Art 10); otherwise voluntary; Italy requires communication of DPO to Garante if designated | Art 37-39 | Operator | Screening below (4.3) | This file (screening only) | decision recorded with reasons; reassess at scale | before-pilot |
| G-29 | Chapter V: which flows leave the EEA (OVH group support/sub-processors, each AI vendor processing and support-access, Cloudflare, Twilio, email, PSP) | Art 44-46, 49 | Operator, each vendor | Section 3 facts | NONE | transfer register per vendor; tool for each (adequacy / SCC / DPF) | BLOCKER |
| G-30 | Transfers to US vendors rely on the EU-US DPF only if the vendor is certified; the General Court dismissed the Latombe challenge (T-553/23, Sept 2025: secondary source), appeal C-703/25 P pending (secondary, verify on InfoCuria); so SCCs + TIA as fallback | Art 45, 46(2)(c), Commission decision on DPF (cite exact OJ reference: to be added by counsel) | Operator | none | NONE | TIA placeholder per vendor; do not depend on DPF alone | BLOCKER |
| G-31 | Transfer Impact Assessment placeholder (law and practice of destination, supplementary measures such as client-side encryption are not possible for vendors that must read prompts) | Art 46, Schrems II line (secondary) | Operator | broker minimises what is sent (ADR-0050) | NONE | one TIA per non-adequate transfer | before-pilot |
| G-32 | Children / age: under-14 needs parental consent for AI access and data processing (L132 Art 4(4)); Art 8 GDPR / Codice privacy Art 2-quinquies (14 years for consent to information-society services: secondary, verify) | L132 Art 4(3),(4); GDPR Art 8 | Operator | none | NONE | simplest control: 18+ only in ToS plus an age gate; the proportionate verification method needs advice (Q4); minors are "vulnerable" (Garante item 6) | BLOCKER |
| G-33 | Retention schedule and minimisation (operator logs, ledger, abuse data, tickets, backups) | Art 5(1)(c),(e) | Operator | ADR-0065 s.11.4 "fixed and short" without values | NONE | numbers and rationale | before-pilot |
| G-34 | Accountability: policies, training of staff with access, DPIA/RoPA/LIA records kept | Art 5(2), 24 | Operator | none | NONE | | before-pilot |
| G-35 | Consent mechanics where used (optional features: telemetry beyond necessity, support access, voice retention, improvement) are granular, withdrawable | Art 7, 4(11) | Operator | none | NONE | design consent screens; ePrivacy Art 5(3) / Codice privacy cookie rules for the web client: check (secondary) | before-pilot |
| G-36 | Data subjects' complaint path and contact point stated in the notice | Art 13(2)(d), 77 | Operator | none | NONE | | before-pilot |
| G-37 | Vendor prompt injection / exfiltration is a security and breach scenario (agent with logged-in browser) | Art 32, 33 | Operator | ADR-0065 T3 (partial) | Design text | model follows injected instruction within allowed destinations: residual risk to record in DPIA | BLOCKER |
| G-38 | Telephony/Twilio and recording consent (calls with third parties) | Art 6, 13/14; national telecom/recording rules (not researched) | Operator/customer | `docs/telephony.md` optional extra | NONE | keep OFF in hosted base plan until separately reviewed | n.a. while off |
| G-39 | Member State representative | Art 27 | Operator | n.a. if the operator is established in the EU/Italy | NONE | operator legal entity and establishment unknown | n.a. (confirm) |

### 4.2 EU AI Act

Role analysis (to be confirmed by a professional): the operator integrates third-party general-purpose AI models into its own
AI system (Jarvis hosted) and offers it under its own name, which makes it the **provider of an AI system** (AIA Art 3 definition;
article text not re-read here: verify) and a **downstream provider** of the GPAI models. It is not (on this design) the provider
of the GPAI model: it does not train, and fine-tuning/modification thresholds under the Commission GPAI guidelines were not
researched (revisit if open-weight models are self-hosted or fine-tuned). The private-capacity customer is **not a deployer**
(the AI Act excludes personal non-professional use from "deployer": verify Art 3(4)); a business customer using it
professionally **is** a deployer. The operator is also a deployer of the vendor models for its own internal use (support tooling),
if any.

Intended purpose statement (proposal, for counsel): "A personal general-purpose assistant that, at the customer's instruction,
reads and writes files, browses the web and operates software inside the customer's own isolated virtual computer, and
converses by text and voice. It is not intended for, and the terms prohibit, use for decisions about third parties in
employment, education, credit/insurance, access to essential public services or benefits, law enforcement, migration,
justice or elections, nor for biometric identification/categorisation or emotion recognition."

| ID | Requirement | Legal source | Responsible party | Implementation | Evidence available | Open gap | Release decision |
|---|---|---|---|---|---|---|---|
| A-01 | Classify operator roles (provider of AI system, downstream provider of GPAI, deployer) and record the reasoning | AIA Art 3 definitions, Art 25 | Operator (counsel) | this section | This file (analysis only) | counsel confirmation; reassess if models are self-hosted/fine-tuned (GPAI provider duties Art 53) | BLOCKER |
| A-02 | Written intended purpose and instructions for use consistent with ToS | AIA Art 3 ("intended purpose"), Art 13 logic by analogy | Operator | statement above | NONE | adopt in ToS/product copy | before-pilot |
| A-03 | Prohibited practices check (Art 5 applies since 2 Feb 2025; (ba),(bb) from 2 Dec 2026): (a)/(b) manipulation or exploiting vulnerabilities; (c) social scoring; (d) crime prediction by profiling; (e) untargeted facial scraping; (f) emotion inference in workplace/education; (g) biometric categorisation of sensitive traits; (ba) non-consensual intimate imagery; (bb) CSAM | AIA Art 5(1) (read in SD art-5); Art 113 amended | Operator | ADR-0056: personality and mood are deterministic, enumerated, no model call, no microphone/camera analysis, no hidden pass, "never a reading of the person", not used to nudge; no image generation in base design; AUP bans the rest | Design text; ADR-0056 tests written, not run | confirm no engine feature infers emotion from voice/images (vendor "realtime" audio models may); AUP wording; image/video generation features, if added, trigger (ba)/(bb) and Art 50 | BLOCKER |
| A-04 | High-risk screening of foreseeable uses (Annex III areas: biometrics, critical infrastructure, education, employment, essential services incl. credit and insurance, law enforcement, migration, justice/democratic processes - read in SD annex-3) | AIA Art 6(2), Annex III, Art 7; applies from 2 Dec 2027 (amended) | Operator (provider); business customer (deployer) | Restriction by ToS/AUP (list above) + technical limits: no biometric ID features, no connectors to HR/credit/benefit decision workflows offered, general-purpose tools only; marketplace/plugins off (ADR-0065 D15) | NONE | a general-purpose assistant can be put to Annex III use by a user; whether this changes the operator's classification depends on intended purpose and on Art 25(1)(c) (deployer who changes purpose becomes provider: verify); Commission guidelines on classification: check status; also check Annex I (not expected) | before-pilot |
| A-05 | Restrict the offering to B2C personal use at start, or define business terms with a deployer clause | AIA Art 3(4), 26 (deployer duties for high-risk only) | Operator | ToS | NONE | Q1 | before-pilot |
| A-06 | Art 50(1): persons interacting directly with an AI system must be informed unless obvious | AIA Art 50(1),(5) (read: info "at the latest at the first interaction", accessibility) | Operator (provider) | UI states Jarvis is an AI; **voice and phone calls to third parties, emails or chats the agent sends to people who are not the customer must also disclose AI interaction**: gap | NONE | design the disclosure for outbound agent communications (email footer, call opening); the customer's own chat is obvious but disclose anyway | before-pilot |
| A-07 | Art 50(2): synthetic audio/image/video/text outputs marked machine-readable and detectable, effective, interoperable (exceptions: assistive editing, no substantial alteration) | AIA Art 50(2); Art 50(2) transition to 2 Dec 2026 only for systems on the market before 2 Aug 2026 (does not cover a new service); COP50 final Code of Practice 10 June 2026 (voluntary; commentators say no single technique suffices: secondary) | Operator (provider) with AI vendors (who may already mark) | none; TTS/STT vendors TBD | NONE | per vendor: does TTS audio carry a watermark/metadata; whether marking plain text emails is in scope; use the Code of Practice as reference | BLOCKER if voice or generated media is in the first release; otherwise before-public |
| A-08 | Art 50(3): deployers of emotion recognition / biometric categorisation systems inform exposed persons | AIA Art 50(3) | Operator | Jarvis mood is SIMULATED, deterministic, driven by owner request/activity/dialogue signals, not emotion recognition (ADR-0056) | ADR-0056 design; tests written not run | keep a design rule: no vendor feature that infers emotion or biometric traits may be enabled; document in intended purpose | before-pilot |
| A-09 | Art 50(4): deepfake and public-interest text disclosure by deployers | AIA Art 50(4) | Customer (business) / operator through AUP | AUP: customer must disclose when publishing | NONE | | before-public |
| A-10 | AI literacy (Art 4, amended): measures to support AI literacy of staff and of persons using the system for the provider/deployer; no specific level required | AIA Art 4 (SD art-4: all paragraphs AMENDED) | Operator | staff briefing; customer-facing explanation of limits (ADR-0058 strip, ADR-0057 first run) | NONE | confirm final wording on EUR-Lex; write the measure | before-pilot |
| A-11 | GPAI downstream information: obtain from each model provider the documentation they must supply to downstream providers; check their usage policies against agentic browser use, resale, EU users | AIA Art 53(1)(b) (upstream duty; applies since 2 Aug 2025); vendor terms (repo: Anthropic D.4 resale clause) | AI vendors (upstream); operator collects | none | NONE | request/collect public model documentation (no contact with vendors is made by this document) | before-pilot |
| A-12 | Human oversight in practice: STOP always visible, live view with takeover, approvals for sensitive tools, per-customer remote STOP layers | AIA Art 14 (high-risk) by analogy; GDPR Art 22/25 | Operator | ADR-0058, ADR-0062 (approvals), ADR-0065 s.12 | Design/code, not run | remote STOP with hostile guest NOT RUN | before-pilot |
| A-13 | Logging and traceability (AIA Art 12/19 are high-risk duties; here good practice and GDPR security) | AIA Art 12 by analogy; GDPR Art 32, 5(2) | Operator | flight recorder/turn ledger on volume; operator logs content-free | Design/code, not run | balance logging vs minimisation | before-public |
| A-14 | Incident handling: serious-incident reporting (AIA Art 73) is a high-risk duty; GPAI systemic risk Art 55 not the operator's; internal incident runbook | AIA Art 73 (n.a. if not high-risk), GDPR Art 33 | Operator | ADR-0065 s.11.5 (open) | NONE | runbook | BLOCKER (as G-26) |
| A-15 | National layer: AgID and ACN as AI authorities; Garante competent for data; L132 principles (Art 3, 4) | L132 Art 3, 4, 20 | Operator | none | NONE | check implementing decrees under L132 delegations (not researched) | before-public |
| A-16 | Re-check of AI Act dates and Commission guidelines at each gate | AIA Art 113 (amended by OMN), SD-TL | Operator | this file s.1.1 | n.a. | owner of the check | each gate |
| A-17 | Registration in EU database / conformity assessment / CE (high-risk) | AIA Art 49, 43 | Operator | n.a. only while A-04 holds | n.a. | if any Annex III use becomes intended, this becomes a major workstream; not costed | n.a. (conditional) |

### 4.3 Screening notes referenced above

**DPIA screening (WP248 rev.01 criteria, as listed on the Garante decision page; and Garante Allegato 1).**

| Criterion | Jarvis Cloud | Met? (provisional) |
|---|---|---|
| 1 evaluation/scoring, profiling | memory/personalisation builds a profile of the customer | arguably yes |
| 2 automated decisions with legal/similar effect | not as designed (acts for the customer); operator suspension: see G-20 | no / partial |
| 3 systematic monitoring | agent reads mail/pages/screens continuously; operator metadata egress logs | arguably yes |
| 4 sensitive or highly personal data | email, finance and health site logins, files | yes |
| 5 large scale | not at pilot; at public launch likely depends on numbers | later |
| 6 matching/combining datasets | memory combines files, mail, web, calendar | yes |
| 7 vulnerable data subjects | consumers; minors if age gate fails (Garante item 6) | risk |
| 8 innovative technology | AI agent with browser control, voice | yes |
| 9 prevents exercising a right/service | no | no |

Four or more criteria (4, 6, 8 plus 1/3) are met, and Garante item 7 applies (AI systems, online voice assistants, plus another
criterion), and item 4/9/10 arguably. **Provisional conclusion: a DPIA is required before real customer data; Art 36 prior
consultation of the Garante to be considered if residual high risk remains. To be confirmed by a qualified privacy professional.**

**DPO screening (Art 37(1)).** Not a public body. (b) core activity "regular and systematic monitoring of data subjects on a
large scale": the core activity is hosting an agent that acts for each customer, not monitoring customers; (c) large-scale special
categories: incidental, not the core purpose. Provisional: no mandatory DPO at pilot size; a voluntary DPO or external adviser is
sensible; reassess at public launch. To be confirmed by a qualified privacy professional.

### 4.4 Chapter V transfer register (placeholder; one row per vendor chosen)

| Vendor | Role | Where processed (documented) | Support/remote access locations | Tool (adequacy / DPF / SCC) | TIA | Status |
|---|---|---|---|---|---|---|
| OVHcloud (VPS) | processor / sub-processor | FR/DE/PL etc. choice of DC; Italy NOT FOUND | EU and Canada per old FAQ (secondary) | adequacy (Canada, limited) / SCC per DPA Annex | placeholder | NOT VERIFIED |
| Anthropic API | processor (per commercial terms, verify) | `global` or `us` (no EU-only) | NOT FOUND | DPF or SCC (verify) | placeholder | NOT VERIFIED |
| OpenAI API | processor (verify) | EU residency by approval; default not EU | NOT FOUND | DPF or SCC | placeholder | NOT VERIFIED |
| Google Gemini (paid) | processor (verify) | "any country" | NOT FOUND | DPF or SCC | placeholder | NOT VERIFIED |
| Mistral | processor (verify) | NOT FOUND | NOT FOUND | NOT FOUND | placeholder | NOT VERIFIED |
| STT/TTS vendor | processor | TBD | TBD | TBD | placeholder | not chosen |
| Backup storage | processor | TBD | TBD | TBD | placeholder | not chosen |
| PSP, email, Cloudflare (OAuth service) | various | TBD | TBD | TBD | placeholder | not chosen |

## 5. Cross-cutting rows (design decisions that carry legal weight)

| ID | Requirement | Legal source | Responsible | Implementation | Evidence | Open gap | Release decision |
|---|---|---|---|---|---|---|---|
| X-01 | Recovery key mode: operator-wrapped key means operator CAN technically decrypt; customer-held key means no recovery | GDPR Art 32, 5(1)(f), 13 | Operator | ADR-0065 s.6, 8 (open decision 4) | NONE | decision + honest notice | BLOCKER |
| X-02 | Engine keys never in the guest: broker holds keys, per-customer token; broker sees all prompts (operator controller-level access to content in transit) | Art 25, 32 | Operator | ADR-0065 s.10 (open decision 3) | NONE | broker must not log content; decide and document | before-pilot |
| X-03 | Egress tier (open web via proxy vs allow-list) determines third-party data exposure and abuse risk | Art 32 | Operator | ADR-0065 s.10 (open decision 2) | NONE | decision | before-pilot |
| X-04 | Marketplace/plugins, vendor CLI subscriptions, `attach` to own Chrome stay OFF | Art 25, 32; vendor ToS | Operator | ADR-0065 D9, D11, D15 (open: confirm) | NONE | confirm and enforce in image | BLOCKER |
| X-05 | Hibernation memory snapshots contain plaintext secrets: avoid at pilot | Art 32 | Operator | ADR-0065 s.11.3 default (i) stop+warm window | Design text | | before-pilot |
| X-06 | Single-VPS baseline: no pre-check done that OVH VPS permits per-VM firewall outside guest, metadata blocking, snapshot API, and AUP allows browser automation/arbitrary code | Art 28, 32 | OVH, operator | ADR-0065 Appendix A (checklist, not evaluated) | NONE | verify in writing from OVH documents | BLOCKER |
| X-07 | Mood/personality honesty: the interface must not present simulated mood as a feeling or use it to influence the owner | AIA Art 5(1)(a), 50 context; GDPR Art 5(1)(a) | Operator | ADR-0056 | design + tests not run | UI copy review | before-pilot |
| X-08 | Budget cap statement truthfulness (lagging vendor billing) | consumer-law (not researched) | Operator | ADR-0066 s.20.1 | NONE | consumer-law review | before-public |
| X-09 | Consumer-protection and contract law (withdrawal right, unfair terms, digital-content rules, VAT/OSS, payment services): out of the requested scope, flagged | not researched | Operator | none | NONE | include in legal review scope | before-public |

## 6. Cost placeholders for the unit-economics file (NOT quotes; no numbers invented)

| Item | Type | Placeholder | Needed by |
|---|---|---|---|
| Privacy/AI-law counsel: role analysis, ToS/AUP, privacy notice, DPA template | one-off + periodic | TBD - get quotes | BLOCKER gate |
| DPIA (drafting, risk workshop, review) and annual refresh | one-off + annual | TBD | BLOCKER gate |
| DPO (voluntary external) or privacy adviser retainer | recurring | TBD / may be zero at pilot | before-pilot decision |
| Review of Art 28 DPAs: OVH, each AI vendor, PSP, email, backup, Cloudflare | one-off per vendor, again on change | TBD per vendor | BLOCKER gate |
| Transfer impact assessments | one-off per non-adequate transfer, refresh when DPF litigation moves | TBD | before-pilot |
| Security maintenance: patching images, Chromium fast-patch path, vulnerability scanning, SBOM | recurring engineering time | TBD | BLOCKER gate |
| Penetration test / independent control tests (isolation, egress, restore, deletion) | one-off + after major change | TBD | before-pilot |
| Backups: storage, egress, restore drills, retention | recurring | TBD (also in infra costs) | BLOCKER gate |
| Rights-request handling (access, export, erasure, objection) | per-request staff time + tooling | TBD per request | before-pilot |
| Breach readiness: on-call, runbook rehearsal, notification tooling | recurring | TBD | BLOCKER gate |
| Age-gate / verification method | per-user or flat | TBD | BLOCKER gate |
| AI Act: literacy measure, labelling/watermark integration, vendor documentation tracking, periodic re-check of legal timeline | recurring | TBD | before-pilot |
| Insurance (cyber/professional): not researched | recurring | TBD | before-public |
| Higher-cost EU-resident inference (e.g. regional endpoint surcharge +10% found in repo for some vendors) | per usage | repo-recorded surcharge, re-verify | vendor choice |

## 7. RELEASE GATES

**Gate 0 - before any real customer data (staff and synthetic data only until all hold)**
1. Roles decided (Q1) and written; B2C-only vs business scope fixed (G-01, A-05).
2. Hosted ToS/AUP, privacy notice (Italian), age policy and gate (G-09, G-11, G-32), intended-purpose statement.
3. DPIA completed and signed off; Art 36 decision recorded (G-27).
4. Art 28 chain: DPAs reviewed and accepted with OVH and every enabled AI/voice/backup/payment/email vendor; only reviewed vendors enabled; sub-processor list (G-05..G-07).
5. Chapter V register with tool and TIA placeholder filled for every non-adequate transfer; no vendor with unknown retention/location enabled for EU customer data (G-29..G-31).
6. RoPA and legal-basis table (G-02, G-10); special-category position documented (G-03, G-04).
7. Security: ADR-0065 controls tested at least for isolation, egress, secrets, deletion, restore, break-glass audit (all currently NOT RUN) on staff environments; OVH capabilities verified in writing (G-23..G-25, X-06).
8. Erasure/export workflow works end to end including backup expiry statement (G-13, G-14, G-16).
9. Breach runbook exists and has been rehearsed once; Garante notification channel confirmed (G-26).
10. Prohibited-practice check and Art 50 position signed off by counsel (A-03, A-06, A-07).
11. Recovery key mode decided and honestly described (X-01); marketplace/attach/CLI-subscription off in the image (X-04).

**Gate 1 - before the external pilot**: rights-request process live (G-13..G-19); sub-processor list and change-notice (G-08); Art 22 human review for suspensions (G-20); retention values set (G-33); consent mechanics (G-35); DPO decision (G-28); vendor GPAI documentation collected (A-11); AI literacy measure (A-10); outbound AI-disclosure for emails/calls (A-06); broker content-free logging (X-02); egress tier (X-03); pen test.

**Gate 2 - before public launch**: re-check of AI Act timeline and Commission guidelines (A-16); full TIA refresh after the DPF appeal status; consumer-law review (X-08, X-09); logging balance (A-13); objection/restriction states (G-17); third-party data-subject position (G-19); national AI-law implementing acts (A-15); reassess DPO and large-scale criteria; insurance.

## 8. Top open legal questions for a professional (see also the final report)

- **Q1** Is the operator controller or processor for the content of private customers' files, browser profile and memory, given the household exemption does not protect it? Is B2C-only viable, and what does a business customer's DPA look like?
- **Q2** Which Art 6 bases and which Art 9(2) conditions can apply to special-category or Art 10 data of third parties that the agent encounters in files and pages, and how is it minimised without monitoring content?
- **Q3** Chapter V: for each AI vendor with a `global`/US inference geo or unknown support access, is DPF/SCC + TIA enough, and may the product be offered as "EU data residency" at all?
- **Q4** Age: 18+ only with what verification; interplay of GDPR Art 8, Codice privacy Art 2-quinquies and L132 Art 4(4).
- **Q5** AI Act: provider/downstream-provider qualification; Art 50(1)/(2) duties for outbound emails, calls and synthetic voice from a general-purpose agent; whether foreseeable Annex III misuse changes the classification and what the AUP must contain.

## 9. What this document is NOT

- It is **not a certification, audit report or legal opinion** and not legal advice. A qualified privacy/AI-law professional must review and confirm every conclusion.
- It does **not** claim that Jarvis, Dodaios, OVHcloud, any AI vendor, payment/email provider or any other party is GDPR- or AI-Act-compliant. "Evidence available: NONE" is the honest state for almost every row; ADR text is intent, not proof; no test or control was run.
- Vendor facts come from the repo's research files (themselves read through a summarising tool) and from search summaries; they are **unverified** and must be re-read on the vendors' own contract documents.
- GDPR article wording was **not re-read from EUR-Lex in this pass** (the fetch returned no body); article numbers are from general knowledge and flagged "verify". AI Act article text was read on the Commission service desk (non-binding) and the Garante list and Italian Law 132/2025 were read in the official documents. The Omnibus text itself was not read; its details are from the service desk and secondary summaries.
- No provider, authority or lawyer was contacted; no account, purchase, build, test or download was made.
- The cost section contains placeholders, not quotes. Consumer-law, tax, telecom/recording, NIS2 and employment-law questions were not researched.

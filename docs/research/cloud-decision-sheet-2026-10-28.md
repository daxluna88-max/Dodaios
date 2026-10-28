# Jarvis Cloud decision sheet v2 (plan block 17), 2026-10-28

Status: research and design baseline. **Nothing was bought, registered, contacted, called or run.** No provider contract
exists. All sources were read on **2026-10-28** (access date). Web pages were read through a summarising fetch tool; the
contract PDFs were downloaded from the public OVHcloud contract URLs and converted to text locally. Figures that matter
must be re-checked on the live page before any commitment. Currencies are never converted silently (see section 6).

**Not closed, must stay visible:** (1) the stack of Jarvis Cloud branches is **unmerged**; (2) privacy blockers **J1/J2**
were corrected in source on branch `claude/block19` **statically, NOT RUN**: they are **not** to be described as closed.
A single pilot customer does not prove scaled economics (see `unit-economics-1-10-50-2026-10-28.md`).

Companion files: `docs/research/unit-economics-1-10-50-2026-10-28.md` (1/10/50 customers),
`docs/research/ai-costs-2026-10-28.md` (R3), `docs/research/cloud-providers-vm-2026-10-28.md` (R1),
`docs/research/cloud-providers-containers-sandboxes-2026-10-28.md` (R2), ADR-0059, ADR-0065, ADR-0066.

## 1. Design baseline (chosen by Dax/GPT, no purchase made)

- One **OVHcloud Linux VPS-2 per customer** (x86-64, 4 vCores, 8 GB RAM, 75 GB NVMe, "standard" region in the EU),
  always-on for the first pilot. Separate guest VM per customer.
- Ubuntu LTS, lightweight X11/Xvfb, session D-Bus, Chromium/Playwright; persistent home/workspace and browser profile.
- Authenticated HTTPS live view via noVNC with human takeover for logins and 2FA.
- Inference keys stay in a **node-side broker outside the customer environment**; every AI/voice call passes server-side
  authorization, worst-case budget reservation, bounded steps and reconciliation; no provider master keys in the guest.
- Egress control against control-plane, private and metadata endpoints.

## 2. Annual-price condition (the most important correction)

The EUR 7.21 ex VAT per month for VPS-2 is **not a no-commitment monthly quote**. It is the monthly equivalent of a
**12-month upfront payment**: EUR 7.21 x 12 = **EUR 86.52 ex VAT**; at 22% Italian VAT = **EUR 105.55** (86.52 x 1.22 =
105.5544). The same page shows "EUR 8.80 incl. VAT" per month (7.21 x 1.22 = 8.796). The configurator links carry the
parameter `upfront12`. No separate month-to-month price was found on any page read (NOT FOUND). Consequences, all
documented in the OVHcloud texts (section 3): billed in full up front; automatic renewal for the same period; early
cancellation means paying what remains; the Italian GTC says no refund for non-use or early stop.

## 3. OVHcloud facts: verified vs GPT's report

| # | Item | What the official source says | Verdict vs GPT |
|---|---|---|---|
| 1 | VPS-2 specs | 4 vCores, 8 GB RAM, 75 GB NVMe, 1 Gbps public bandwidth (ovhcloud.com/it/vps, /en/vps) | CONFIRMED |
| 2 | Price | IT page: "Da 7,21 EUR + IVA/mese", 8.80 incl. VAT; configurator parameter 12-month upfront. EN pages show USD 8.50 (US/world store) | CONFIRMED, but it is the 12-month-upfront rate; monthly-only price NOT FOUND |
| 3 | 12-month total | 86.52 ex VAT; 105.55 at 22% (my arithmetic, matches GPT) | CONFIRMED (arithmetic) |
| 4 | Renewal price | Docs: commitment "will automatically renew for the same price and terms"; the Italian VPS terms: automatic renewal for a period of the same duration. Price on the live order page at renewal time: NOT FOUND | partly; renewal price figure NOT FOUND |
| 5 | Region "standard" | Page text: "13 European datacentre locations, including France" (standard) and "Local Zones" (VPS-2 only, "28 cities"). Local Zones lack NVMe, monitoring, Additional IP, automated backup. The pages never define "standard region" in those words | PARTLY: the standard/Local Zone distinction exists; names of the 13 sites NOT FOUND on the pages read |
| 6 | Italy/Milan for VPS | "VPS Italy" marketing page offers "13 geolocations across Europe", no names. Milan is named only for Managed Kubernetes in another doc; not for VPS | NOT FOUND (Italy datacentre for VPS not confirmed). France (Gravelines, Strasbourg), Warsaw, Limburg named elsewhere |
| 7 | Included backup | Standard: one daily automatic backup "of the previous 24 hours", included; exported and replicated 3 times within the same datacentre on separate servers; system disk only (additional disks excluded); not available in Local Zones. Based on live VPS snapshots; the QEMU guest agent prepares the filesystem (not installed by default on most distros). Restore from Control Panel; "a few minutes to several hours" depending on used disk; mount read-write copy possible. Premium (paid): 7 rolling daily backups, chosen time (UTC) | PARTLY CONFIRMED: included backup is a single 24-hour copy, **same datacentre**, not off-instance, no crash-consistency guarantee beyond guest-agent. Pages are inconsistent about "last seven" for standard. OVHcloud's own terms (Annex 1): backup "does not under any circumstances constitute a guarantee against the loss of Client data"; customer must run its own BCP/DRP |
| 8 | Premium backup / snapshot price | "From USD 1.40/month" premium, "from USD 0.40/month" snapshot (US/world page). EUR price NOT FOUND; the IT page gives no price | USD only; EUR NOT FOUND |
| 9 | Traffic / DDoS / IPv4 | Unlimited traffic ("excluding Asia-Pacific datacentres" which have quotas); anti-DDoS included; dedicated IPv4 and IPv6 included. Terms: OVHcloud may throttle to 1 Mbps until the end of the billing period in case of excessive use | CONFIRMED, plus the 1 Mbps clause |
| 10 | OS images | "Linux, Windows, BSD", turnkey distributions "Ubuntu, Debian, Fedora" listed on IT page; exact Ubuntu LTS version offered NOT FOUND on pages read | Ubuntu CONFIRMED as a family; LTS version NOT FOUND |
| 11 | Root access | Terms: "The Client is the sole administrator of the VPS"; Control Panel shows reinstall with SSH key. A literal "root" statement: NOT FOUND | effectively confirmed (sole administrator) |
| 12 | Nested virtualisation / KVM | Not required for baseline. Nothing found. | NOT FOUND |
| 13 | API provisioning | Ordering through `POST /order/cart`, `POST /order/cart/{id}/vps`, `.../checkout` described in a US OVHcloud support guide (page returned 403 on direct read; known only via search extract, indirect). An official CLI doc lists `ovhcloud vps terminate`. REST terminate path: NOT FOUND | PARTLY; an API exists for ordering, reinstall (with SSH key) and a terminate request; exact EU endpoints and parameters NOT verified |
| 14 | Cancel / refund of annual | Docs: commitments "billed annually and up front only"; cancel at end of period; early cancellation only by "paying the remainder"; cancel up to 24 h before the end date. Italian GTC art. 11.4: no refund for non-use or early stop; for commitment periods the full amount stays due; non-consumer clients waive termination for convenience (art. 12.4). Consumer 14-day withdrawal right (art. 12.4 IT) exists but the customer is invited to waive it at order | **A prepaid annual commitment cannot be cancelled or refunded by the operator** (business customer). Refund for undelivered services only |
| 15 | SLA | 99.9% advertised; contract credits: monthly availability >=99.5% and <99.9% -> 10% credit of the monthly amount; <99.5% -> 50%. "Unavailability" = no access and no reboot for more than 3 consecutive minutes after the customer opens a ticket; OVHcloud has a "reasonable endeavours" obligation | CONFIRMED figure; the remedy is a credit capped at 50% of one month, not damages |
| 16 | Contracting entity / law | Italian GTC (version 17 Jan 2025): contract with **OVH S.r.l., Milan**, Italian law and Italian courts; DPA (version 17.10.2025): also OVH SRL. The English "world" PDF I first opened is the Quebec/Canada edition (Canadian law): it does **not** apply to an Italian customer. | NEW: which entity and law apply depends on the customer account country |
| 17 | AUP (automation, scraping, arbitrary code, ports) | GTC art. 3.5 (IT/EN texts): use "in a reasonable manner"; forbidden: SPAM, "intrusion or attempted intrusion", cryptocurrency mining, denial-of-service, video game bots, traffic exchanges, Black Hat SEO, mass download of illicit content. **No clause about web scraping or browser automation was found.** VPS terms: the customer must not harm other clients or the reputation of the host IP; OVHcloud may cut the Internet connection of a VPS with an unpatched flaw or a compromise until reinstall. Art. 7: OVHcloud may block the SMTP port after spam; third block can mean no re-enable until the end of the service. Older VPS terms (v3.0 FR, third-party summary) mention filtering of sensitive ports, proxy services: not in the current 10 Apr 2025 text I read | no scraping ban found; the "SPAM", "intrusion", "bots" and IP-reputation clauses are the practical risks for an agent that browses and logs in on behalf of customers. Legal reading not provided |
| 18 | DPA / GDPR | DPA exists (OVH SRL, v17.10.2025). Customer = controller, OVHcloud = processor. Datacentre location chosen at order, not changed without approval. OVHcloud and authorised sub-processors may process remotely for service and security. For non-European datacentres standard contractual clauses apply; EU datacentres: SCCs apply "if the Service terms foresee processing from a country without adequacy decision" (DPA 6.5). Annex lists OVH affiliates in Canada and Asia-Pacific as sub-processors | CONFIRMED that a DPA and SCC mechanism exist. Complete sub-processor country list and which VPS support functions use non-EEA affiliates: NOT verified (annex only partly read) |
| 19 | Company jurisdiction | OVH group is French (OVH SAS); the contracting entity for Italy is OVH S.r.l. (Milan). Canadian affiliates are documented sub-processors/importers | CONFIRMED; US CLOUD Act exposure of the group: NOT FOUND (not assessed) |
| 20 | Delete / data | Billing continues for disabled-but-not-deleted VPS; billing ends when the VPS is deleted at the end of the period; backups are deleted at VPS end; cancelled-VPS data archived 30 days (product page, one source) | CONFIRMED (one source for the 30 days) |

Discrepancies to report honestly: (a) the 7.21 is an annual-upfront rate, as GPT said; (b) "standard region" is not
defined by OVHcloud in those words; (c) Italy is **not** confirmed for VPS; (d) the included backup is one 24-hour copy in
the same datacentre; (e) no EUR snapshot or premium-backup price was found; (f) refunds of the annual payment are not
offered to a business customer under the Italian GTC; (g) the SLA 99.9% is the advertised target, credits are the sole documented remedy.

## 4. DOCUMENTED vs ASSUMED numbers

| Number | Value | Status | Source / basis |
|---|---|---|---|
| VPS-2 price (12-month upfront, per month) | EUR 7.21 ex VAT | DOCUMENTED | ovhcloud.com/it/vps |
| Same incl. 22% VAT | EUR 8.80 | DOCUMENTED | same |
| 12-month payment | EUR 86.52 ex VAT / 105.55 incl. | DERIVED | arithmetic |
| Monthly no-commitment price | - | NOT FOUND | |
| Premium backup, snapshot | USD 1.40 / USD 0.40 "from" | DOCUMENTED (USD only) | ovhcloud.com/en/vps |
| Stripe Italy EEA standard card | 1.5% + EUR 0.25 | DOCUMENTED | stripe.com/en-it/pricing |
| Stripe Billing | 0.7% of Billing volume | DOCUMENTED | same |
| Stripe Tax | 0.5% per transaction (or EUR 0.45 via API) | DOCUMENTED | same |
| Stripe premium EEA card / dispute fee | 2.8% + 0.25 / EUR 20 per dispute | DOCUMENTED | same |
| AI cost per customer, light / medium | LLM+voice USD 3.73 / 16.23 (combo Haiku 5.5 90% + Sonnet 5.5 10%) | DOCUMENTED prices, ASSUMED usage | R3 section 6 |
| Off-instance backup storage per customer | EUR 1.50 per month | ASSUMED placeholder | not documented (OVH object-storage price page truncated: NOT FOUND) |
| Gateway/broker/monitoring node | one VPS-2 up to 10 customers, two at 50 | ASSUMED sizing | capacity not measured |
| Domain + email | EUR 3 per month | ASSUMED placeholder | |
| Control-plane DB/backup | EUR 2 per month | ASSUMED placeholder | |
| Compliance one-off (legal review, DPIA, DPO screening) | EUR 5,000 | PLACEHOLDER, not a quote | |
| Compliance recurring | EUR 100/month (200 at 50) | PLACEHOLDER | |
| Hours: security maintenance | 4 / 8 / 20 h per month at 1 / 10 / 50 | ASSUMED | |
| Support | 1 h per customer per month | ASSUMED | |
| Labour rate | EUR 40/h | PLACEHOLDER (opportunity cost, not a salary) | |
| USD to EUR | 1 USD = 1 EUR | ASSUMED; no FX documented | |
| EUR 60 as tax-inclusive B2C price | net 49.18, VAT 10.82 | DERIVED from the owner's reference (not validated) | ADR-0059 |

## 5. Modest-usage scenarios (AI costs from R3, combo B; USD treated 1:1 as EUR)

Per-customer variable cost per month (EUR): VPS 7.21 + off-instance backup 1.50 (assumed) + AI + Stripe 1.57
(= 1.5% x 60 + 0.25 + 0.7% x 60, on the gross price, standard EEA card).

| Scenario | AI (LLM+voice) | Variable cost | Net revenue 49.18 minus variable (contribution before shared costs and labour) |
|---|---|---|---|
| Light (20 turns/day, 5 browser steps/day, no voice) | 3.73 | 14.01 | **35.17** |
| Medium (80 turns/day, 30 steps/day, 60 voice min) | 16.23 | 26.51 | **22.67** |
| Intense (250 turns/day, 100 steps/day, 300 voice min) | 52.68 | 62.96 | **-13.78** (loss) |
| Light, cheap model only (Haiku 5.5, no router) | 1.31 | 11.59 | 37.59 |
| Medium, cheap model only | 6.31 | 16.59 | 32.59 |

Sensitivity of the medium case to FX (AI cost x k): k=0.85 -> 25.10; k=1.00 -> 22.67; k=1.15 -> 20.24. Without prompt
caching R3 adds about 50% to the AI line. The share of the strong model dominates AI cost (R3 6.7). Voice via realtime
APIs would cost more than the cascade assumed (R3 6.5).

## 6. Net / gross economics (VAT 22%, B2C tax-inclusive)

If EUR 60 is the tax-inclusive price paid by an Italian consumer: net = 60 / 1.22 = **EUR 49.18**; VAT = 60 - 49.18 =
**EUR 10.82** (owed to the state, not revenue). If EUR 60 were an ex-VAT B2B price, net would be 60 and the customer
would pay 73.20; this was not the stated case.

Cost lines are net of VAT **only if the operator can deduct input VAT**. If not, the VPS costs 8.80 instead of 7.21
(+1.59 per VM per month). Stripe fees are taken on the gross 60.

Allowance view (illustrative, from the coordination note; NOT a quote): hosting + off-instance backup + share of
gateway/monitoring EUR 12; AI+voice 15; payment/other 3; contingency 5 => 35; remainder of 49.18 = **14.18** for support and
margin. **A EUR 20 margin is not established**: it is not reached by this allowance (14.18 < 20), and the 14.18 must still pay
support, security, compliance and engineering (see the 1/10/50 file, where the same inputs give a negative result at 1 and 10
customers when labour is counted).

## 7. What is NOT yet known

- Monthly (non-annual) price; the renewal price; whether the price differs by region.
- Whether OVHcloud offers VPS in Italy; names of the 13 standard European sites; which is the "standard" site for the pilot.
- EUR price of snapshot and premium backup; price and S3 compatibility of OVH object storage for off-instance backups.
- Whether the AUP tolerates automated logged-in browsing on behalf of third parties at fleet scale; IP reputation handling;
  whether port 25 and other sensitive ports are filtered on the current VPS range.
- Nested virtualisation, exact Ubuntu LTS image, exact EU API endpoints for order and terminate, rate limits of the API.
- Full DPA sub-processor list and the actual non-EEA processing for VPS support; DPIA outcome; legal reading of the terms.
- Real usage, real AI cost with routing, caching and screenshots, support load; whether Chromium+desktop fits in 8 GB for
  concurrent long tasks; noVNC latency; restore time of the included backup.
- That the unmerged stack runs; that J1/J2 are fixed at runtime (static correction only, NOT RUN).
- Anthropic and other vendors' EU residency/DPA positions (see R3 section 9).

## 8. Sources (all accessed 2026-10-28)

OVHcloud: ovhcloud.com/it/vps/ ; ovhcloud.com/en/vps/ ; ovhcloud.com/en/vps/compare/ ; ovhcloud.com/en/vps/vps-backup/ ;
ovhcloud.com/en/vps/vps-france/ ; ovhcloud.com/en/vps/vps-italia/ ; ovhcloud.com/it/vps/best-vps/ ;
ovhcloud.com/it/terms-and-conditions/contracts/ ; blog.ovhcloud.com/en/posts/vps-2027/ ;
docs.ovhcloud.com/en/guides/bare-metal-cloud/virtual-private-servers/using-automated-backups-on-a-vps ;
docs.ovhcloud.com/en/guides/account-and-service-management/managing-billing-payments-and-services/manage-commitments ;
.../how-to-cancel-services ; github.com/ovh/ovhcloud-cli/blob/main/doc/ovhcloud_vps_terminate.md ;
contract PDFs: contract.eu.ovhapis.com/1.0/pdf/contrat_genServices-it.pdf (GTC Italy, 17 Jan 2025),
.../Conditions_Particulieres_VPS-it.pdf (VPS terms, 10 Apr 2025), .../OVH_Data_Protection_Agreement-it.pdf (DPA, 17.10.2025),
.../contrat_genServices-we.pdf and .../Conditions_Particulieres_VPS-we.pdf (world/Canada edition, not applicable to Italy);
support.us.ovhcloud.com (ordering and cancel guides, indirect via search; direct read 403).
Stripe: stripe.com/en-it/pricing. AI: `docs/research/ai-costs-2026-10-28.md` and its sources.

---

# Appendix A. Superseded broad survey (the previous version of this sheet, kept verbatim except heading levels)

The text below is the earlier sheet, written before the baseline was chosen. It lists many providers and is kept for
reference only; where it disagrees with sections 1-8 above, the sections above prevail.

## Jarvis Cloud — decision sheet (plan block 17), 2026-10-28

Status: research summary for the owner. **No provider is chosen. No price here was verified by an official live page
unless the detail files say so; all figures come from the three research reports and must be re-checked on the
provider's page before any decision.** Nothing was bought, registered or called. Currencies are NOT converted (USD and
EUR are shown as found).

Detail files (sources, access dates, NOT FOUND lists, limits):

- `docs/research/cloud-providers-vm-2026-10-28.md` (R1: VPS, VMs, bare metal, microVM layers, about 30 providers)
- `docs/research/cloud-providers-containers-sandboxes-2026-10-28.md` (R2: containers, agent sandboxes, browser services, cloud desktops)
- `docs/research/ai-costs-2026-10-28.md` (R3: model, voice and GPU unit costs)
- `docs/adr/0065-jarvis-cloud-runtime-isolation-security.md` (blocks 18, 19, 21)
- `docs/adr/0066-jarvis-cloud-budget-and-mobile.md` (blocks 20, 22) and `dodaios/cloud/budget_ledger.py`

### 1. What the owner asked for

Each customer's assistant must work like a physical PC (create folders and files, run a real logged-in browser,
shell, GUI automation, long tasks, persistent storage) and the service must cost little. The customer is Italian, so
EU residency and a DPA are preferred.

### 2. Complete list of services found (names only; details in the reports)

**A. Virtual machines, VPS, bare metal (R1):** Hetzner (Cloud, dedicated, server auction), OVHcloud, Scaleway,
Infomaniak, Aruba Cloud, IONOS, netcup, Contabo, Hostinger, Strato, Vultr, Akamai/Linode, DigitalOcean, UpCloud,
Exoscale, AWS (EC2, Lightsail), Google Cloud, Azure, Oracle Cloud (incl. Always Free), IBM Cloud, Alibaba Cloud,
Tencent Cloud, Seeweb, Register.it, TIM, Fastweb, OpenMetal, latitude.sh, Equinix Metal (closed 2026-06-30), Apple/Mac
cloud (not applicable). Virtualisation layers: Firecracker, Cloud Hypervisor, Kata, gVisor, Proxmox/KVM, Incus/LXD.

**B. Containers and PaaS (R2):** Fly.io Machines, Fly Sprites, Google Cloud Run, GKE Autopilot, AWS Fargate, AWS App
Runner (not researched), Azure Container Apps, Azure Container Instances, Railway, Render, Koyeb, Northflank,
Scaleway Serverless Containers, OVH containers, Cloudflare Containers, DigitalOcean App Platform, Heroku.

**C. Agent sandboxes (R2):** E2B, Daytona, Modal Sandboxes, Runloop, CodeSandbox, Replit, Blaxel, Morph, Vercel
Sandbox, Anthropic computer use, OpenAI computer use, Gemini computer use (not researched), Azure dynamic sessions.

**D. Browser as a service (R2):** Browserbase, Steel, Browserless, Anchor Browser, Hyperbrowser, Kernel, Cloudflare
Browser Rendering, AWS Bedrock AgentCore Browser, Apify, Bright Data; no Google or Azure product found.

**E. Cloud desktop (R2):** AWS WorkSpaces for AI agents, AWS AppStream, AWS WorkSpaces Personal, Windows 365, Azure
Virtual Desktop, Google Cloud Workstations, Shadow, Paperspace, Kasm Workspaces, Apache Guacamole/noVNC (self-hosted).

**F. Model APIs, voice, GPU (R3):** Anthropic, OpenAI, Google Gemini, Mistral (EU), Cohere, xAI, DeepSeek,
Alibaba/Qwen, Moonshot, Groq, Together, Fireworks, DeepInfra, OpenRouter, Scaleway/OVH/Infomaniak/IONOS AI endpoints,
AWS Bedrock, Azure OpenAI, Vertex AI; voice: Voxtral, ElevenLabs, Deepgram, AssemblyAI, Cartesia and others;
GPU rental: RunPod, Vast.ai, Lambda, Hetzner GPU, OVH GPU, Scaleway GPU, Modal, Replicate, Fly GPU.

### 3. Cheapest documented options per architecture (from the reports; assumptions in each file)

**Architecture "one VM per customer, deleted/stopped when idle" (R1; 20% use = 146 h/month, 40 GB state, EUR ex VAT):**

| # | Option | EUR/month | Caveat |
|---|---|---|---|
| 1 | Hetzner CX23 | 1.86 | live page says "Currently unavailable" |
| 2 | Scaleway DEV1-M | 4.23 | price from a search snippet (official page 403); billing when stopped not confirmed |
| 3 | Hetzner CPX22 (2 vCPU/4 GB) | 5.13 (5%: 1.71, 60%: 14.24) | orderable; Hetzner raised prices twice in 2026 |
| 4 | Seeweb 4G | 7.01 | stopped-disk cost NOT FOUND |
| 5 | DigitalOcean 2 vCPU/4 GB | 7.61 USD | |

Hetzner Cloud, DigitalOcean and Akamai bill stopped VMs too; the saving needs "snapshot and delete" (my assumption;
restore time and Chromium profile survival not verified). Hetzner Cloud has no nested virtualisation (no Firecracker).
Always-on (architecture a): about 3.81 EUR (OVH VPS-1, 12-month commitment) to 35 EUR. Bare-metal with microVMs
(architecture c, Hetzner AX42 79 EUR/month): 1.84 / 2.26 / 7.18 EUR per customer at 5/20/60%, excluding backup, second
host and engineering.

**Full PC-like environment on container/sandbox platforms (R2; 1 vCPU, 2 GB, 10 GB, compute+disk, USD ex VAT):**

| # | Option | 5% | 20% | 60% | Caveat |
|---|---|---|---|---|---|
| 1 | Fly.io Machines (suspend) | 2.47 | 4.48 | 9.83 | US region price; no Milan |
| 2 | Northflank | 2.37 | 5.97 | 15.57 | assumes stopped service bills no compute (not stated); always-on 25.17 |
| 3 | Vercel Sandbox | 3.18 | 11.21 | 32.64 | US region price |
| 4 | E2B | 2.98 | 11.92 | 35.77 | needs Pro plan 150 USD/month |
| 5 | Daytona | 3.37 | 12.31 | 36.16 | only one with documented Computer Use API (Xvfb, xfce, VNC) |

Browser-as-a-service gives only a browser (no file system or shell), about 0.06-0.09 USD/h, to be added to a sandbox.
No platform except Daytona documents a headed Chromium/Xvfb; for the rest it is a technical inference.

**AI model cost per customer (R3; USD/month, LLM only, 90% cheap model / 10% strong model, assumptions stated):**

| Combination | light (20 turns/day) | medium (80) | intense (250) |
|---|---|---|---|
| Haiku 5.5 only | 1.31 | 5.40 | 16.97 |
| Haiku 5.5 + Sonnet 5.5 | 3.73 | 15.32 | 48.12 |
| Mistral Small 4 + Medium 3.5 (EU vendor) | 3.39 | 13.95 | 43.85 |
| gpt-6-luna + gpt-6.1-sol | 3.47 | 14.27 | 44.86 |
| DeepSeek flash + v4-pro | 2.14-4.27 | 8.79-17.59 | 27.63-55.27 |

Voice cascade about 0.0152 USD/min (cheaper 0.0043); realtime voice APIs 0.04-0.08 USD/min. Without prompt caching add
about 50%. The strong-model share dominates the cost.

### 4. Indicative total per customer (hosting + AI), unconverted, illustrative only

Adding the cheapest documented hosting (about 2-6 USD/EUR) to the cheap-model AI cost gives roughly 4-10 per month at
light use and 10-25 at medium use; intense use with a strong model share exceeds 50. This is an order of magnitude from
documented numbers plus my usage assumptions, NOT a quote, NOT a margin calculation and NOT a promise of a monthly cap.
Support, engineering, payments, taxes, backups, a second host, egress, IPv4 and the live view are not included.

### 5. What the research could not settle

- Official prices not readable (403/404/JS): AWS, GCP, Azure, Vultr, Contabo, UpCloud EC2-class prices (third party only),
  Exoscale in EUR, Hetzner auction list, Render, Koyeb, CodeSandbox, Morph, Bright Data, Shadow, Scaleway official.
- EU/Milan prices: Cloud Run, GKE, Fargate, Azure Container Apps/Instances, Google Workstations, Fly EU, Vercel EU.
- DPA links and AUP/ToS on browser automation, scraping, arbitrary code: read only for Hetzner (no mention of scraping)
  and a few DPA links; for all others this is an open owner/legal decision.
- Model vendors without found retention/region/DPA policy: DeepSeek, xAI, Cohere, Alibaba, Groq. Anthropic documents no
  EU-only inference; OpenAI EU residency only after commercial approval (+10%). Gemini free tier not suitable for EU
  multi-tenant. Subscriptions are not API credit; Anthropic's terms forbid resale and credential sharing; OpenAI and
  Google primary texts were not readable.
- Service-wide: nested virtualisation per provider, hard spending caps, usage-record latency, stopped-environment billing.
- Every number was read through a summarising fetch tool: re-verify on the live page.

### 6. Decisions for the owner (consolidated)

1. Architecture: microVM or VM per customer with persistent volume and snapshot/scale-to-zero (recommended by the
   analysis in ADR-0065), or a lighter shared path for tool-less chat.
2. Provider family: plain VMs (needs delete-and-restore to avoid idle cost), container platform with suspend, agent
   sandbox, or bare metal with microVMs (needs nested virtualisation or bare metal).
3. Region: EU only (and Italy preferred or not), which excludes or reprices several options.
4. Egress policy: open web through a deny-list proxy, or allow-list only (or by plan).
5. Engine keys: inside the guest or only in a node-level proxy/broker (recommended; also gives the enforced budget).
6. Encryption and account recovery: operator-wrapped key (recoverable) or customer-held key (not recoverable).
7. Marketplace, vendor-CLI subscriptions and browser "attach" mode stay off in the cloud (confirm).
8. First login to sites: live view through a gateway or cookie import.
9. Hibernation: stop plus warm window, or memory snapshot (contains plaintext secrets).
10. Which AI vendors are acceptable for EU customer data (retention, training, DPA, region), and the strong-model share.
11. Voice in the first release or text only; live view of the desktop in the first release or not.
12. Budget rules: no row = no reservation; accruing costs may exceed the cap then block; who bears late usage records;
    plan caps and currency after the provider is chosen.
13. Legal: controller/processor roles, DPA, ToS/AUP, sub-processors, DPIA (analysis only here, not legal advice).
14. Payment provider and email login method; notification channels.

Nothing has been decided, bought, registered or sent to any provider.

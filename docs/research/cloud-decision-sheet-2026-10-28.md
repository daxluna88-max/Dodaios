# Jarvis Cloud — decision sheet (plan block 17), 2026-10-28

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

## 1. What the owner asked for

Each customer's assistant must work like a physical PC (create folders and files, run a real logged-in browser,
shell, GUI automation, long tasks, persistent storage) and the service must cost little. The customer is Italian, so
EU residency and a DPA are preferred.

## 2. Complete list of services found (names only; details in the reports)

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

## 3. Cheapest documented options per architecture (from the reports; assumptions in each file)

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

## 4. Indicative total per customer (hosting + AI), unconverted, illustrative only

Adding the cheapest documented hosting (about 2-6 USD/EUR) to the cheap-model AI cost gives roughly 4-10 per month at
light use and 10-25 at medium use; intense use with a strong model share exceeds 50. This is an order of magnitude from
documented numbers plus my usage assumptions, NOT a quote, NOT a margin calculation and NOT a promise of a monthly cap.
Support, engineering, payments, taxes, backups, a second host, egress, IPv4 and the live view are not included.

## 5. What the research could not settle

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

## 6. Decisions for the owner (consolidated)

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

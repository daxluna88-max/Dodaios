# R2 - Container / PaaS / agent-sandbox / browser-as-a-service / cloud-desktop providers

Research only. NO decision. Date of research and of all "accessed" dates: **2026-10-28**.
Scope: everything that is NOT a plain VPS, for a hosted "Jarvis Consumer" service where each customer's assistant behaves like a physical PC (files, persistent Chromium profile, shell, GUI automation, long tasks, persistent storage), low cost, Italian customer, EU/GDPR preferred, mostly-idle usage.

Method and rules: public web only (WebFetch/WebSearch). No sign-ups, no API calls, no code run. Prices are quoted exactly as shown on the page fetched; "NOT FOUND" means the official page fetched did not give the number (often because the pricing page renders prices with JavaScript). "unverified (third party)" = number comes from a non-official site or a search snippet. Currency is USD unless stated. Official pages mostly show prices ex-VAT; Italian VAT (22%) would be added to consumer/B2C invoices - treat every figure as ex-VAT unless the source says otherwise.

---

## 1. Summary table

Legend for PC-fit: **FULL** = files + persistent disk + shell + (can run Chromium/Xvfb) is plausible on the product; **PART** = only part (e.g. browser only, or ephemeral disk); "(inferred)" = the product is a general Linux VM/container, GUI/Chromium headed is NOT documented on the page I read but is technically plausible - needs a test by the owner (I did not test anything).

### (a) Containers / PaaS / serverless / Kubernetes

| # | Provider | Isolation | Scale-to-zero / suspend | Persistent disk | EU / Italy | Headline price (documented) | PC-fit |
|---|---|---|---|---|---|---|---|
| a1 | Fly.io Machines | Firecracker microVM | stop + suspend (memory snapshot, <=2 GB RAM) | Volumes $0.15/GB-mo | Paris, Frankfurt, Amsterdam, Stockholm, London; no Milan | shared-cpu-2x 2 GB $13.39/mo (iad price) | FULL (inferred) |
| a2 | Fly.io Sprites | microVM ("full Linux computer") | auto sleep when idle, checkpoint | 100 GB POSIX disk, pay per use | regions NOT FOUND | CPU $0.0385/h, RAM $0.021875/GB-h | FULL (inferred) - listed also in (b) |
| a3 | Google Cloud Run | gVisor-based sandbox (general knowledge, not on page read) | request- or instance-based, scale to 0 | none native (GCS FUSE / NFS volumes - not read) | Milan (europe-west8) is a Cloud Run region | official EU price NOT FOUND (page JS-rendered); Tier 2 = unverified (third party) | PART (no durable local disk) |
| a4 | GKE Autopilot | container (gVisor optional) / VM nodes | pods only billed while running; cluster fee | PD volumes | Milan region exists (not re-verified) | cluster fee $0.10/h, $74.40/mo credit (unverified third party); Pod rates for Milan NOT FOUND | FULL (inferred), high ops burden |
| a5 | AWS Fargate / ECS | Firecracker microVM per task | tasks run on demand | ephemeral 20 GB default; EFS/EBS optional | Milan, Frankfurt, etc. exist; EU price NOT FOUND | US-East x86: $0.000011244/vCPU-s, $0.000001235/GB-s | PART/FULL (inferred) |
| a6 | AWS App Runner | container | pause/scale | none | not researched | NOT RESEARCHED | PART |
| a7 | Azure Container Apps | container (Hyper-V isolation not verified) | scale to 0 | Azure Files mounts (not read) | Italy North/West Europe exist; rates NOT FOUND (placeholders "$-") | free grant 180,000 vCPU-s + 360,000 GiB-s + 2M req/mo | PART |
| a8 | Azure Container Instances | container group | stop/start | Azure Files | rates NOT FOUND | per-second billing, vCPU rounded up | PART |
| a9 | Railway | container | no documented scale-to-zero in page read | Volumes $0.15/GB-mo | EU region NOT FOUND on page | $0.00000772/vCPU-s, $0.00000386/GB-s | FULL (inferred) |
| a10 | Render | container | no scale-to-zero for web services (not on page) | Disks (single instance) | Frankfurt | NOT FOUND officially; unverified (third party): Starter $7, Standard $25, disk $0.25/GB | FULL (inferred) |
| a11 | Koyeb | Firecracker microVM (general knowledge) | scale-to-zero | Volumes (price NOT FOUND) | Frankfurt (page lists 7 regions) | CPU instance price NOT FOUND; Pro $29/mo | PART/FULL (inferred) |
| a12 | Northflank | microVM + gVisor on all plans | pause not verified | NVMe disk $0.11657/GiB-mo | EU region NOT FOUND on page | $0.01667/vCPU-h, $0.00833/GB-h | FULL (inferred) |
| a13 | Scaleway Serverless Containers | container | scale to 0 | ephemeral only | Paris/Amsterdam/Warsaw (FR company) | EUR 0.00001/vCPU-s, EUR 0.000002/GB-s (snippet; official page 403) | PART (no durable disk) |
| a14 | OVHcloud AI Deploy / containers | container | per-minute | Object Storage | FR company, EU | only doc examples (EUR 0.03/h per CPU, illustrative) | PART |
| a15 | Cloudflare Containers | container VM (Durable-Object-bound) | sleeps after timeout | ephemeral (persistence NOT documented) | location control NOT FOUND | mem $0.0000025/GiB-s, CPU $0.00002/vCPU-s (active), disk $0.00000007/GB-s | PART (no durable disk) |
| a16 | DigitalOcean App Platform | container | always on | no persistent storage on page | regions NOT FOUND on page | $5/mo 1vCPU/512MiB ... $50/mo 2vCPU/4GiB | PART |
| a17 | Heroku | dyno (container) | Eco sleeps after 30 min | ephemeral FS (not on page) | Dublin not on page | Eco $5, Basic $7, Standard-1X $25 | NOT a fit (ephemeral) |

### (b) Agent sandboxes

| # | Provider | Isolation | Pause/persist | Max session | EU | Headline price | PC-fit |
|---|---|---|---|---|---|---|---|
| b1 | E2B | Firecracker (general knowledge) | pause keeps FS + memory indefinitely | 1 h Hobby / 24 h Pro (reset on pause) | shared EU cluster on Pro+, on request | $0.000014/vCPU-s + $0.0000045/GiB-s; Pro $150/mo | FULL (inferred; E2B Desktop referenced by OpenAI docs) |
| b2 | Daytona | container; VM sandboxes (Linux/Windows) | stop/archive; VM state offloaded | not stated | EU (blog: Frankfurt, London); docs generic "eu" | $0.0504/vCPU-h, $0.0162/GiB-h, storage $0.000108/GiB-h (5 GiB free) | FULL - documented Computer Use API (Xvfb, xfce4, x11vnc, noVNC) |
| b3 | Modal Sandboxes | gVisor (general knowledge) | filesystem snapshots, Volumes | up to 24 h per sandbox | `eu` 1.15x, `eu-west/north/south` 1.75x | $0.00003942/core-s (sandbox), $0.00000667/GiB-s | PART/FULL (GUI not documented) |
| b4 | Runloop | devbox | Suspend/Resume on Pro $250/mo | not stated | NOT FOUND | CPU $0.108/h, RAM $0.0252/GB-h | FULL (inferred) |
| b5 | CodeSandbox SDK | Firecracker (third party) | hibernation | - | - | NOT FOUND (page 403); Pro $9 vs $12 conflict (third party) | NOT FOUND |
| b6 | Replit | container | - | - | - | Core $20-25/mo (conflicting third party); no programmatic API found | NOT a fit |
| b7 | Blaxel | sandbox (microVM per vendor, not verified) | auto-suspend ~5 s, resume ~25 ms; Volumes $0.12/GB-mo | not stated | EU NOT FOUND | $0.0000115/GB-s of RAM; up to $200 credits | FULL (inferred) |
| b8 | Morph Cloud | - | - | - | - | NOT FOUND (404) | NOT FOUND |
| b9 | Vercel Sandbox | Firecracker microVM | persistent sandboxes + snapshots + Drives | 45 min Hobby / 24 h Pro (resets) | fra1, cdg1, arn1, lhr1, dub1 | Active CPU $0.128/h, RAM $0.0212/GB-h (iad1; EU rates NOT FOUND) | FULL (inferred) |
| b10 | Anthropic computer use | reference Docker + Xvfb | n/a (self-hosted) | n/a | n/a | tool itself no fee, tokens only | reference only |
| b11 | OpenAI computer use | partner-hosted (Browserbase, Browser Use, Daytona, E2B Desktop) | - | - | NOT addressed | hosted env price NOT FOUND on OpenAI page | not a hosted PC |
| b12 | Google Gemini computer use | - | - | - | - | NOT RESEARCHED | - |
| b13 | Azure Container Apps dynamic sessions | Hyper-V sandbox (not verified) | ephemeral | - | - | "per session-hour" rate NOT FOUND | PART |

### (c) Browser-as-a-service

| # | Provider | Persistent profile | Max session | Concurrency | EU | Price per browser-hour | Has FS + shell? |
|---|---|---|---|---|---|---|---|
| c1 | Browserbase | Contexts (cookies/localStorage/IndexedDB; no FS) | Free 15 min; paid NOT FOUND | 3 / 25 / 100 / 250+ | EU Central, UK listed | Developer $20/mo incl. 100 h, then $0.12; Startup $99 incl. 500 h, then $0.10 | no |
| c2 | Steel | Profiles API exists (not read) | Launch 15 min, Scale 1 h, Ent 24 h | 10 / 100 / 1000+ | NOT FOUND | $0.10 (Launch), $0.08 (Scale $250/mo) | no |
| c3 | Browserless | persisted sessions 1-90 days | 2 / 15 / 30 / 60 min by plan | 2-100 | London/Amsterdam endpoints; EU on Enterprise | units: $25/mo=20k units (1 unit=30 s) | no |
| c4 | Anchor Browser | profiles (cookies/localStorage/cache) | max_duration NOT FOUND | 5-500+ | custom regions on Enterprise | $0.09 (calculator); Starter $50/mo | no |
| c5 | Hyperbrowser | profiles (persistChanges) | NOT FOUND | 1 / 25 / 100 | NOT FOUND | $0.10/h; Startup $30/mo | no |
| c6 | Kernel | profiles NOT read | standby after 5 s idle | plan-dependent (NOT FOUND) | NOT FOUND (regional on Start-Up+) | headless $0.06, headful $0.48, headful+GPU $2.88 | no |
| c7 | Cloudflare Browser Rendering ("Browser Run") | none documented | no fixed max; keep_alive <=20 min | 10 incl. / 200 max | NOT FOUND | 10 h/mo incl. then $0.09 (+$2/extra concurrent) | no |
| c8 | AWS Bedrock AgentCore Browser | Browser Profiles (cookies/localStorage in S3, S3 rates) | NOT FOUND | NOT FOUND | Frankfurt, Ireland, London, Milan, Paris, Spain, Stockholm (Built-in Tools row) | $0.0895/vCPU-h + $0.00945/GB-h, per second on actual CPU | no |
| c9 | Apify | Actors (storage) | - | - | NOT FOUND | CU = 1 GB-h = $0.20 (Free/Starter), $0.16 (Scale) | partial (Actor storage) |
| c10 | Bright Data Browser API | NOT FOUND (404) | - | - | - | NOT FOUND | - |
| c11 | Google / Azure equivalent managed browser | not found as a product | - | - | - | NOT FOUND | - |

### (d) Cloud desktop / DaaS

| # | Provider | OS | Unit | EU | Price (documented) | Notes |
|---|---|---|---|---|---|---|
| d1 | Amazon WorkSpaces for AI agents (+ AppStream/WorkSpaces Applications streaming instances) | Linux (Rocky/RHEL/Ubuntu Pro) and Windows | per-second instance + MCP session fee | Frankfurt, Ireland, London, Milan, Paris, Spain | MCP session $0.05/h (EU confirmed), 10 free h/region/mo; Linux stream.standard.medium $0.084/h US-East; stopped instance $0.025/h | 200 GB storage per instance; agent sees screen/click/type |
| d2 | Amazon WorkSpaces Personal | Ubuntu/Amazon Linux/Windows | monthly or hourly | Milan etc. not on page | Ubuntu Power example (Oregon): $7.25/mo + $0.66/h | persistent desktop |
| d3 | Windows 365 | Windows (Linux not mentioned) | per user/month | NOT FOUND | Enterprise 2 vCPU/4 GB/64 GB $28.00 user/mo | flat |
| d4 | Azure Virtual Desktop | Windows 10/11 (and Linux hosts via VM) | VM compute | Italy North | per-VM Linux rates NOT FOUND; third party D4as v5 ~ $125.56/mo East US (unverified) | |
| d5 | Google Cloud Workstations | Linux | control plane + VM + disk | Milan region exists | $0.20/h per cluster (~$144/mo), $0.05/vCPU-h mgmt fee, us-central1 (unverified, search snippet of Google page) | cluster fee is shared |
| d6 | Shadow PC | Windows | - | - | NOT FOUND (404) | consumer product |
| d7 | Paperspace (DigitalOcean) | Linux CPU servers / Windows desktops | hourly/monthly | NOT FOUND | C1 1 vCPU/512 MB $0.0045/h; $6-$788/mo | closer to a VPS |
| d8 | Kasm Workspaces | Linux container desktops | license | self-host anywhere | Community free (5 concurrent sessions); Starter $10/user or $20/session; Enterprise quote | licence only; you still pay infrastructure |
| d9 | Apache Guacamole / noVNC | any | Apache-2.0, v1.6.0 (2025-06-22) | self-host | free | gateway only (VNC/RDP/SSH over HTML5) |

---

## 2. Per-provider detail

Format: facts are from the pages cited in section 4. "HQ" is general knowledge unless a page is cited, and was not verified this session. Founded / funding: **NOT FOUND (not sourced)** for all providers - I did not research it.

### (a) Containers / PaaS / serverless

**a1 Fly.io Machines.** HQ: US (general knowledge). Regions EU: ams, arn, cdg, fra, lhr (no Milan) [docs.fly.io/reference/regions]. Isolation: Firecracker microVM (suspend page says Firecracker snapshots). Persistence: Volumes $0.15/GB-mo provisioned, also billed when machine stopped; stopped/suspended machines bill rootfs $0.15/GB-mo; snapshots $0.08/GB-mo with 10 GB free. Suspend: full memory snapshot, resume in a few hundred ms, only for machines <=2 GB RAM, snapshots are not guaranteed (discarded on deploy / host move) so design for cold start; suspended machine bills storage only. Prices (30-day month, iad/ewr): shared-cpu-1x 256 MB $2.19; 512 MB $3.69; 1 GB $6.70; shared-cpu-2x 2 GB $13.39; performance-1x 2 GB $33.00; extra RAM $6/GB-mo. Per second billing. Egress: Europe and N. America $0.02/GB, inbound free. Free tier: none for new orgs (trial = 2 h or 7 days). EU-region prices are NOT FOUND (page shows iad/ewr; "prices vary by region"). Max session: no limit (machines are long running). Concurrency: not stated. API: Machines API (general knowledge; not opened). GUI: not documented; standard Docker/VM so Xvfb+Chromium is plausible. DPA: Fly staff say a DPA is available on request (support@fly.io) or from dashboard "Compliance" menu [community.fly.io + fly.io/compliance.md in search results]. ToS/AUP on automation/scraping: NOT FOUND. Caveats: shared CPUs are throttled; suspend limited to <=2 GB; volumes are pinned to one host/region (single point, snapshots only); no Milan.

**a2 Fly.io Sprites** (sprites.dev -> fly.io/sprites). "Full Linux computers designed for agents". Billing only while running; warm/cold = no compute charge. CPU $0.0385/CPU-h, RAM $0.021875/GB-h, hot storage $0.000683/GB-h (~$0.50/GB-mo), cold storage $0.000027/GB-h (~$0.02/GB-mo). Bandwidth not metered. $30 trial credit. Hero plan $100/mo includes 1,200 CPU-h + 4,800 GB-h + 150 GB, 100 concurrent running Sprites, creation 10/min PAYG. Disk: standard POSIX FS, 100 GB volume, backed by object storage; automatic checkpoints (~1 s, copy-on-write, disk only, "not a process resume"). Wake on HTTP request; idle timer reset by HTTP/TCP/output. API: REST + JS/Go/Python/Elixir SDK + CLI. Regions: NOT FOUND. GUI/browser: NOT FOUND. Per-Sprite CPU/RAM size: NOT FOUND (I assumed 1 CPU/2 GB in the cost model - this is an assumption). DPA: presumably Fly.io's (not verified for Sprites).

**a3 Google Cloud Run.** HQ US; Milan europe-west8 listed as Cloud Run region. Official page fetched was truncated/JS: EU price NOT FOUND. Search snippet of the official page: instance-based free tier 240,000 vCPU-s and 450,000 GiB-s per month (us-central1 basis). Third-party (unverified): Tier 1 instance-based $0.000018/vCPU-s and $0.000002/GiB-s; Tier 2 about 1.2x ($0.0000216 and $0.0000024); I could not confirm which tier Milan is in. No durable local disk (container FS ephemeral); persistence needs GCS/Filestore (not researched). Max request timeout 60 min etc. NOT researched. DPA: Google Cloud DPA exists (not fetched).

**a4 GKE Autopilot (+ gVisor/Kata).** Cluster management fee $0.10/h and $74.40/mo free-tier credit per billing account (unverified, search snippet of Google page); Pod vCPU $0.0445/h and memory $0.0049225/GiB-h (unverified, unofficial mirror, region unknown). Milan prices NOT FOUND. GKE Sandbox (gVisor) / Kata on managed K8s: not researched beyond general knowledge. Needs significant ops work for per-customer pods + PVCs.

**a5 AWS Fargate / ECS.** US-East (N. Virginia) per second: x86 vCPU $0.000011244, GB $0.000001235; ARM vCPU $0.0000089944, GB $0.0000009889; ephemeral storage above 20 GB $0.0000000308/GB-s. Spot up to 70% off (Linux, ECS). Milan/Frankfurt/Paris/Ireland prices NOT FOUND (regional table did not load). Isolation: Firecracker microVM per task (general knowledge). Persistence: EFS/EBS (not researched). No suspend: a task is either running or gone (state must be kept on EFS/EBS). Region support: Milan exists (see AgentCore region table which shows Milan as an AWS region). DPA: AWS GDPR DPA exists (not fetched).

**a6 AWS App Runner:** NOT RESEARCHED.

**a7 Azure Container Apps.** Consumption: per-second vCPU/GiB, idle rate if min replicas, scale to zero = no charge; free grant 180,000 vCPU-s, 360,000 GiB-s, 2M requests/month per subscription; Dedicated plan hourly. Rates NOT FOUND (page cells "$-"). Dynamic sessions (Python/Node/Shell, per session-hour) rate NOT FOUND.

**a8 Azure Container Instances.** vCPU billed per second, rounded up to whole vCPU; memory rounded to tenth GB; rates NOT FOUND; third-party East US about $0.0000125/vCPU-s and $0.0000015/GB-s (unverified).

**a9 Railway.** Plans: Free $1/mo credit (0.5 GB), Hobby $5 ($5 usage), Pro $20 ($20 usage). Usage: CPU $0.00000772/vCPU-s ($20/vCPU-mo), memory $0.00000386/GB-s ($10/GB-mo), volumes $0.15/GB-mo, egress $0.05/GB, object storage $0.015/GB-mo. Volume max: Hobby 5 GB, Pro 1,000 GB. Regions NOT FOUND on page. Scale-to-zero/suspend: NOT FOUND on page.

**a10 Render.** Official page did not load prices. Regions (official docs): Oregon, Ohio, Virginia, Frankfurt, Singapore. Disks: single instance, no zero-downtime deploys, daily snapshot kept 7 days, increase only; disk price not on the docs page. Third party (unverified): Starter $7, Standard $25 (2 GB/1 CPU), disk $0.25/GB-mo. No scale-to-zero on paid web services (not on page).

**a11 Koyeb.** Pro $29/mo (includes $10 compute), Scale $299/mo, per-second billing, scale-to-zero, egress 1 TB incl then $0.02/GB EU/US. CPU instance prices NOT FOUND (only GPU shown). Regions: Frankfurt (Postgres list) and "7 regions in US, EU, Asia" for compute. Volumes "included in plans" without price.

**a12 Northflank.** $0.01667/vCPU-h, $0.00833/GB-h; plans e.g. nf-compute-100-2 (1 vCPU / 2 GB) $0.03333/h = $24/mo. Disk NVMe $0.11657/GiB-mo. Egress $0.06/GB. Free Sandbox tier: up to $20/mo resources. "Every workload isolated, on every plan" microVM + gVisor. BYOC $0.02083/vCPU-h. EU regions NOT FOUND on pricing page. Whether stopped/paused services are free: NOT FOUND (the cost model below shows both cases).

**a13 Scaleway Serverless Containers (FR).** Search snippet quoting the official pricing page (the page itself returned 403): EUR 0.00001 per vCPU-s after 200,000 vCPU-s free/month; EUR 0.000002 per GB-s after 400,000 GB-s free/month; ingress/egress free; ephemeral storage free; prices ex tax. Regions nl-ams, fr-par, pl-waw (general knowledge). The FAQ is internally inconsistent on memory rate. Treat as unverified (search snippet).

**a14 OVHcloud AI Deploy / containers (FR).** Billing per minute; docs examples use EUR 0.03/h per CPU (illustrative only). Official rate NOT FOUND. Managed Kubernetes control plane price NOT FOUND.

**a15 Cloudflare Containers.** Requires Workers Paid ($5/mo). Included: 25 GiB-h memory, 375 vCPU-min, 200 GB-h disk. Overage: memory $0.0000025/GiB-s, CPU $0.000020/vCPU-s (active usage only), disk $0.00000007/GB-s; billed in 10 ms increments; sleeps after timeout. Instance types: lite 1/16 vCPU 256 MiB; basic 1/4 vCPU 1 GiB; standard-1 1/2 vCPU 4 GiB 8 GB; standard-2 1 vCPU 6 GiB 12 GB; standard-3 2 vCPU 8 GiB 16 GB; standard-4 4 vCPU 12 GiB 20 GB. Egress NA/EU $0.025/GB (1 TB incl.). Limits: 6 TiB concurrent memory, 1,500 vCPU, 30 TB disk per account, 50 GB image storage; snapshots up to 20 GB kept 30 days. Whether instance disk is persistent: NOT FOUND (page does not say; treat as ephemeral). Worker + Durable Object billed separately.

**a16 DigitalOcean App Platform.** $5 (1 vCPU/512 MiB, 50 GiB transfer), $10 (1 GiB), $12, $25 (2 GiB), $50 (2 vCPU/4 GiB); overage $0.02/GiB. Persistent storage not on page; regions not on page.

**a17 Heroku.** Eco $5 (0.5 GB, sleeps after 30 min), Basic $7, Standard-1X $25, Standard-2X $50 (1 GB), Performance-M $250 (2.5 GB). Ephemeral dyno FS (general knowledge). Not suitable for persistent PC.

### (b) Agent sandboxes

**b1 E2B.** Hobby $0 (one-time $100 credit, 20 concurrent, 1 h session, 10 GiB storage); Pro $150/mo (100 concurrent, 24 h session, 20 GiB storage, custom CPU/RAM); Pro+ $650 (600 concurrent); Pro++ $1,150 (1,100); Enterprise from $3,000/mo. Usage $0.000014/vCPU-s, $0.0000045/GiB-s; storage free; sizes 1-8 vCPU, 1-8 GiB; default 2 vCPU/4 GiB = about $0.166/h. Pause: default saves filesystem + memory, kept indefinitely, no TTL; pause ~4 s per GiB RAM, resume ~1 s; running limit resets on pause/resume. Cost while paused: NOT FOUND. EU: shared EU cluster for Pro and above on request; E2B Cloud runs on Google Cloud in US/EU/APAC; BYOC (AWS/GCP VPC) on Enterprise. DPA: NOT FOUND (search returned no E2B legal page). Open-source infra repo can be self-hosted with Terraform (search snippet). OpenAI docs list "E2B Desktop" as a desktop sandbox for computer use.

**b2 Daytona.** Per hour, billed per second: vCPU $0.0504, memory $0.0162/GiB, storage $0.000108/GiB-h (5 GiB free); Windows $0.0858/vCPU-h; $200 free compute, no card. Tier limits (default region): Tier 1 10 vCPU/10-or-20 GiB/30 GiB (docs inconsistent), Tier 2 100 vCPU/200 GiB/300 GiB (needs card + $25), etc.; creation rate 300/min. Stopped container sandboxes occupy disk quota until archived; archived = quota freed and billing stops; stopped VM sandboxes offload state to storage. Computer Use API documented for Linux and Windows (Xvfb, xfce4, x11vnc, noVNC; mouse/keyboard/screenshots/recording); Chromium not mentioned. Regions: blog lists EU-Central Frankfurt and EU-West London; docs show generic "eu"; contact sales for non-default shared regions. DPA published (daytona.io/dpa, updated 2025-08-22); ToS (daytona.io/terms-of-service). AUP: NOT FOUND.

**b3 Modal Sandboxes.** Sandbox rates (non-preemptible, 3x list per page): CPU $0.00003942 per physical core (2 vCPU)-s, memory $0.00000667/GiB-s, min 0.125 core. Starter $0 + compute with $30/mo free credit, up to 100 containers; Team $250/mo + compute, $100 credit, 5,000 containers. Region selection multiplier: `eu` (EEA) 1.15x; `eu-west`, `eu-north`, `eu-south` 1.75x. Max sandbox timeout 24 h; filesystem snapshots recommended for state beyond 24 h; Volumes mountable; idle_timeout parameter. Data plane stays in selected region; control plane in us-east. DPA: modal.com/legal/dpa (effective 2025-10-31). GUI: NOT documented on the page read. 60 s default container keep-alive is billed.

**b4 Runloop.** Basic free + usage; Pro $250/mo (Suspend/Resume, 1 TB storage); $50 trial credit. CPU $0.108/CPU-h, memory $0.0252/GB-h, devbox storage $0.00034236/GB-h; storage keeps accruing while suspended. Regions NOT FOUND; Enterprise can deploy to customer VPC.

**b5 CodeSandbox SDK.** Pricing page 403. NOT FOUND. Third-party: Firecracker microVMs with an SDK; Pro price disagrees ($9 vs $12).

**b6 Replit.** Core $20-25/mo (conflicting third party), usage-metered; no public programmatic environment API found. Not a provisioning platform for per-customer PCs on available evidence.

**b7 Blaxel.** Sandbox compute $0.0000115 per GB RAM-second (allocated memory); snapshot storage $0.20/GB-mo; images $0.045/GB-mo; Volumes $0.12/GB-mo; Agent Drive free in beta; idle sandboxes suspend after ~5 s, resume ~25 ms (FAQ); $200 free credits; Tier 0 = 10 sandboxes; HIPAA add-on $250/mo. EU regions NOT FOUND.

**b8 Morph Cloud:** pricing URL 404. NOT FOUND.

**b9 Vercel Sandbox.** Pro: Active CPU $0.128/h (only CPU actually used, I/O wait excluded), provisioned memory $0.0212/GB-h (each vCPU includes 2 GB; 1-minute min), creations $0.60/1M, snapshot storage $0.08/GB-mo, Drive storage $0.05/GB-mo (iad1), drive reads $0.0015/GB, writes $0.004/GB, data transfer included in Flat Rate CDN (Pro), downloads free. Hobby: 5 h active CPU, 420 GB-h, 45 min sessions, 10 concurrent. Pro: max 8 vCPU/16 GB, 64 GB ephemeral NVMe per sandbox, 24 h session (resets on stop/resume, so persistent sandboxes are unbounded), 10,000 concurrent, snapshots expire after 30 days unused by default (configurable to never). Persistent sandboxes resume from latest snapshot; Drives up to 1 TiB, mounted by up to 4 per sandbox. 19 regions incl. fra1, cdg1, arn1, lhr1, dub1 (no Milan). EU rates "vary by region" and the regional table is NOT FOUND in fetched text. Pro $20/mo credit is plan-level. DPA: vercel.com/legal/dpa (Enterprise/Pro per third-party blog - verify). Chromium/headed: NOT documented on the pages read. Snapshot is stored in the sandbox region (good for residency; cannot move).

**b10 Anthropic computer use.** Not a hosted product: the docs give a reference Docker + Xvfb implementation; "no additional charge" for the capability, you pay model tokens (screenshots as tokens). The security perimeter is the developer's. Model price: NOT FETCHED from official page (third-party $3/$15 per M tokens is unverified).

**b11 OpenAI computer use.** Official guide lists partner-hosted environments (Browserbase, Browser Use Cloud, Daytona, E2B Desktop) and a separate Agents API hosted browser; "hosted environment may charge separately"; EU data residency not addressed. A DevDay roundup claiming hosted sandboxes $0.03 (1 GB) to $0.48 (16 GB) per 20-minute session is unverified (third party).

**b12 Google Gemini computer use:** NOT RESEARCHED.

**b13 Azure Container Apps dynamic sessions:** exists (Python, Node.js, Shell), per session-hour; rate NOT FOUND.

### (c) Browser-as-a-service

**c1 Browserbase.** Free $0 (1 h, 3 concurrent, 15 min/session, 7-day retention); Developer $20/mo (100 h, overage $0.12/h, 25 concurrent, 1 GB proxy, 30 days); Startup $99 (500 h, $0.10/h, 100 concurrent); Scale custom (250+). Regions: US East/West, EU Central, UK. Contexts: persist Chromium user-data dir (cookies, localStorage, IndexedDB, SW, autofill); HTTP cache not persisted; no direct FS API; encrypted at rest; never expire until deleted; do not run two sessions on the same context at once. Session max for paid plans: NOT FOUND. DPA/ToS: search did not surface; NOT FOUND. Cannot give the agent a shell.

**c2 Steel.** Launch $0 + usage ($30 credit; 10 concurrent; 15 min sessions; $0.10/h; proxy $10/GB; 7-day retention); Scale $250/mo ($100 credit; 100 concurrent; 1 h; $0.08/h); Enterprise 24 h sessions. Regions NOT FOUND. Open-source/self-host (general knowledge, not fetched).

**c3 Browserless.** Units of up to 30 s of browser time: Free 1k units, 2 min; Prototyping $25/mo (20k units, 15 min, 10 concurrent, extra $0.0020/unit); Starter $140 (180k, 30 min, 40); Scale $350 (500k, 60 min, 100); annual saves 30%. Endpoints SF, London, Amsterdam on Free; EU on Enterprise explicit. Self-host commercial licence: Enterprise, price NOT FOUND.

**c4 Anchor Browser.** Free; Starter $50/mo (50 credits, up to 25 concurrent); Team $500; Growth $2,000 (SOC2 Type 2, ISO27001, GDPR); $0.09/browser-hour in calculator; session 38 min assumed (max NOT FOUND). Profiles persist cookies/localStorage/cache; sticky IP profiles. Custom regions incl. EU on Enterprise.

**c5 Hyperbrowser.** Free 5,000 credits (1 concurrent); Startup $30 (30,000 credits, 25 concurrent); Scale $100 (100 concurrent); $0.10/browser-hour (100 credits); proxy $10/GB. Profiles with persistChanges. Max session / regions NOT FOUND.

**c6 Kernel.** Developer free + usage ($5 credit); Hobbyist $30 ($10 credit); Start-Up $200 ($50 credit). Headful $0.0001333336/s (~$0.48/h), headless $0.0000166667/s (~$0.06/h), headful+GPU ~$2.88/h. Standby after 5 s idle = no usage cost. Regional browsers on Start-Up+. Concurrency, profiles: NOT FOUND (separate pages).

**c7 Cloudflare Browser Rendering ("Browser Run").** Free 10 min/day, 3 concurrent; Paid: 10 h/mo included then $0.09/h; 10 concurrent included then $2.00 each (monthly average of daily peak). Limits: 200 concurrent (paid), 3 new/s, keep_alive up to 20 min, 60 s default inactivity timeout. Persistent profile: NOT FOUND.

**c8 AWS Bedrock AgentCore Browser.** $0.0895/vCPU-h + $0.00945/GB-h, per second on actual CPU, peak memory; 1 s minimum; Browser Profiles (cookies/local storage stored in S3, billed S3 rates from 2026-04-15). Built-in Tools row available in Frankfurt, Ireland, London, Milan, Paris, Spain, Stockholm. Session limits/quotas page failed to load: NOT FOUND. AgentCore Runtime microVMs: $0.0895/vCPU-h (v1) or $0.1276 (v2 consumption); sessions up to 14 days; Runtime Instances with EBS persistent volumes (Frankfurt, Ireland only).

**c9 Apify.** Free $0 ($5 usage), Starter $19, Scale $199, Business $999; 1 CU = 1 GB-hour = $0.20 (Free/Starter), $0.16 (Scale), $0.13 (Business); Actor max RAM 16-512 GB. Data residency NOT FOUND.

**c10 Bright Data Browser API:** page 404 - NOT FOUND.

**c11 Google / Azure managed browser products:** none found; NOT FOUND.

### (d) Cloud desktop / DaaS

**d1 Amazon WorkSpaces for AI agents** (GA announced 2026-06). Agents connect via MCP, see the screen, click and type; BYO agent. MCP session fee $0.05/h billed per second while connected, same in all listed Regions incl. EU (Frankfurt, Ireland, Milan; list also London, Paris, Spain); first 10 h per Region per month free; Windows Server fleets also $6.42/user/mo. Streaming instances priced separately (AppStream rates): Linux stream.standard.medium $0.084/h (the page's own example uses $0.088), Ubuntu Pro stream.standard.small $0.098/h, per second with 15-min minimum, no user fee for Linux; On-Demand fleets: stopped-instance fee $0.025/h; each instance has 200 GB included storage (extendable to 500 GB). EU instance rates NOT FOUND (US-East used in cost model). Persistence of user state: not addressed for agent product; AppStream home folders on S3 (general knowledge). Session max: not stated. Isolation: VM per streaming instance.

**d2 Amazon WorkSpaces Personal.** Ubuntu Power example in Oregon: $7.25/mo + $0.66/h. EU prices NOT FOUND. Persistent desktop, AutoStop pauses billing.

**d3 Windows 365 Enterprise.** 2 vCPU/4 GB/64 GB: $28.00 per user/month. Linux not available per page; Business/Frontline not on page.

**d4 Azure Virtual Desktop.** Microsoft states AVD VMs pay Linux compute rates for Windows 11/10 session hosts; no per-VM rates retrieved. Third party: D4as v5 about $125.56/mo East US (unverified).

**d5 Google Cloud Workstations.** Cluster control plane $0.20/h per cluster (~$144/mo) regardless of use; $0.05/vCPU-h management fee on running workstations; PD billed while stopped; us-central1 basis (search snippet). VM rates NOT FOUND. Inactivity auto-stop available.

**d6 Shadow PC:** page 404, NOT FOUND (consumer cloud PC product; not a programmatic per-customer platform as far as known).

**d7 Paperspace (DigitalOcean).** CPU hourly $0.0045 (C1: 1 vCPU/512 MB) to $1.60 (C10: 32 vCPU/244 GB); monthly $6-$788; Linux only for CPU servers; desktops include Windows license. EU not on page.

**d8 Kasm Workspaces.** Community Edition free, max 5 concurrent sessions, free for testing, non-profits and non-commercial per licence docs (so commercial service needs a paid licence); Starter $10/user or $20/session (fewer than 25); Enterprise quote and the only tier listing Kasm SaaS; Developer API: Community and Enterprise only (not Starter). Self-hosted: you supply servers.

**d9 Apache Guacamole / noVNC.** Guacamole: Apache-2.0, clientless HTML5 gateway for VNC/RDP/SSH, v1.6.0 released 2025-06-22. noVNC was not fetched.

---

## 3. Cost per customer (formula, documented prices only)

### Common assumptions (apply to every calculation below)
- Target "PC" = **1 vCPU + 2 GB RAM + 10 GB persistent disk** (minimum I consider realistic for headed Chromium + Xvfb; this is my assumption, not a measured requirement).
- Month = **720 h (30 days)**; duty cycle D = share of the month the environment is running / session is connected: **5% = 36 h, 20% = 144 h, 60% = 432 h**.
- Compute only, ex-VAT, no egress, no LLM tokens, no proxies. Platform-level fixed fees (shared by all customers) are listed separately and NOT included in per-customer numbers.
- Where an EU price was NOT FOUND, the US/default price is used and flagged "EU rate not verified".
- Compute is billed only for running hours (requires real stop/suspend); idle hours billed only for storage as stated.
- Cost is shown as `compute + storage` in USD/month.

### (a) Container / PaaS - cheapest 2 documented
Fixed formula: `cost = D*720*hourly + storage`.

| Option | Hourly (arithmetic) | Storage | 5% | 20% | 60% |
|---|---|---|---|---|---|
| Fly.io Machine shared-cpu-2x 2 GB (iad price, EU not verified) | 13.39/720 = $0.018597/h | 10 GB volume 1.50 + ~2 GB rootfs 0.30 = $1.80 (rootfs size assumed) | 36*0.018597=0.67 +1.80 = **$2.47** | 144*0.018597=2.68 +1.80 = **$4.48** | 432*0.018597=8.03 +1.80 = **$9.83** |
| Northflank nf-compute-100-2 (1 vCPU/2 GB) - ONLY if stopped service bills no compute (NOT verified) | 24/720 = $0.03333/h | 10 GiB * 0.11657 = $1.17 | 36*0.03333=1.20 +1.17 = **$2.37** | 144*0.03333=4.80 +1.17 = **$5.97** | 432*0.03333=14.40 +1.17 = **$15.57** |
| Northflank always-on fallback (if no pause) | $24.00 flat | $1.17 | $25.17 | $25.17 | $25.17 |

Next in line (not top 2): Scaleway Serverless Containers EUR 0.0504/h for 1 vCPU+2 GB (3600*(0.00001+2*0.000002)), but no durable disk and unverified source; Railway $0.0556/h (3600*(0.00000772+2*0.00000386)) + volume $1.50; Cloudflare Containers standard-1 about $0.047/h (4 GiB: 0.036 + disk 0.002 + CPU 25% active 0.009).
Feasibility: Fly = general Linux microVM with volume and suspend: files + persistent profile (on volume) + shell yes; GUI via Xvfb is inferred, not documented; EU price/Milan unverified. Northflank: full containers with disks; pause/EU not verified.

### (b) Agent sandboxes - cheapest 2 documented
| Option | Hourly (arithmetic) | Storage | 5% | 20% | 60% |
|---|---|---|---|---|---|
| Vercel Sandbox Pro, iad1 rates (EU rates NOT FOUND), 1 vCPU/2 GB, CPU 25% active | 0.128*0.25 = 0.032 + 2*0.0212 = 0.0424 -> $0.0744/h | Drive 10 GB*0.05 = $0.50 | 36*0.0744=2.68 +0.50 = **$3.18** | 144*0.0744=10.71 +0.50 = **$11.21** | 432*0.0744=32.14 +0.50 = **$32.64** |
| Fly.io Sprites, assumed 1 CPU/2 GB | 0.0385 + 2*0.021875 = $0.08225/h; hot storage 10*0.000683 = $0.00683/h awake; cold 10*0.000027 = $0.00027/h | see formula: hot hours + cold hours | 36*0.08225=2.96 +36*0.00683=0.25 +684*0.00027=0.18 = **$3.39** | 144*0.08225=11.84 +0.98 +576*0.00027=0.16 = **$12.98** | 432*0.08225=35.53 +2.95 +288*0.00027=0.08 = **$38.56** |

Tied just above: E2B, Daytona and Blaxel all compute to $0.0828/h for 1 vCPU/2 GB (E2B: 0.000014+2*0.0000045=0.000023/s*3600; Daytona: 0.0504+2*0.0162; Blaxel: 2 GB*0.0000115*3600). E2B 5/20/60% = 36*0.0828=**$2.98** / 144*0.0828=**$11.92** / 432*0.0828=**$35.77** (storage free; $150/mo Pro platform fee needed for >1 h sessions). Daytona adds (10-5 free)*0.000108*720 = $0.39: **$3.37 / $12.31 / $36.16**. Runloop: 0.108+2*0.0252=$0.1584/h.
Feasibility: Daytona documents a Computer Use API (Xvfb/xfce/VNC) = the only sandbox here with documented GUI automation. E2B/Vercel/Sprites/Blaxel: files + shell + persistence yes, GUI inferred. Vercel and E2B: EU only on specific plans/regions (E2B EU on request on Pro+).

### (c) Browser-as-a-service - cheapest 2 documented
`cost = D*720 * price per browser-hour` (browser time only).

| Option | $/browser-h | 5% (36 h) | 20% (144 h) | 60% (432 h) |
|---|---|---|---|---|
| Kernel headless | 0.06 (0.0000166667*3600) | **$2.16** | **$8.64** | **$25.92** |
| Steel Scale (marginal; platform fee $250/mo with $100 credit shared across all customers) | 0.08 | **$2.88** | **$11.52** | **$34.56** |

Also: Cloudflare Browser Rendering $0.09/h after 10 h/mo included per account (**$3.24 / $12.96 / $38.88** marginal, plus $5/mo plan and concurrency fees); Anchor $0.09; Browserbase $0.10-$0.12; Hyperbrowser $0.10; Kernel headful $0.48 (**$17.28 / $69.12 / $207.36**); AgentCore Browser about 0.0895 + 2*0.00945 = $0.1084/h upper bound (billed on actual CPU, so lower).
Feasibility: **PART only**. These give a remote Chromium (some with persistent profile) but NO file system for the assistant, NO shell, NO desktop GUI automation (except headful Kernel/AgentCore live view). They would have to be paired with one of (a)/(b) for files and shell, so costs ADD to, not replace, the sandbox cost. Session caps (15 min free, 1 h Scale) are a problem for long tasks.

### (d) Cloud desktop - cheapest 2 documented
| Option | Formula | 5% | 20% | 60% |
|---|---|---|---|---|
| Amazon WorkSpaces for AI agents + AppStream Linux On-Demand (US-East instance rate as proxy; EU instance rate NOT FOUND; MCP $0.05 EU confirmed) | running hours: D*720*(0.084 instance + 0.05 MCP); stopped hours: (720 - D*720)*0.025; 10 free MCP hours ignored; 200 GB storage included | 36*0.134=4.82 + 684*0.025=17.10 = **$21.92** | 144*0.134=19.30 + 576*0.025=14.40 = **$33.70** | 432*0.134=57.89 + 288*0.025=7.20 = **$65.09** |
| Windows 365 Enterprise 2 vCPU/4 GB/64 GB | flat $28.00 per user-month | **$28.00** | **$28.00** | **$28.00** |

Also documented: WorkSpaces Personal Ubuntu Power example $7.25 + D*720*0.66 (Oregon): $31.01 / $102.29 / $292.37. Kasm Starter $10/user licence (+ infrastructure not included).
Feasibility: these ARE full desktops (persistent storage + GUI), but W365 is Windows-only with no documented automation API; AWS agents product is the only one built for agent control, but the stopped-instance fee ($0.025/h = $18/mo) dominates at low duty and instance-per-user model is expensive for an idle-most-of-the-time customer.

### Observations on cost (not a decision)
Of the options with documented prices that can plausibly hold files + persistent browser profile + shell + a virtual display, the per-customer compute at 5% duty is in the range of roughly $2.4-$3.4/month (Fly Machines, Northflank-if-pausable, Vercel Sandbox, Sprites, E2B, Daytona), rising to roughly $4.5-$13 at 20% and $10-$39 at 60%; Fly Machines is lowest throughout because it has the cheapest per-hour rate and a stopped-state cost limited to disk, but it needs the owner to build the lifecycle/orchestration and to accept shared-CPU throttling and suspend limits. Browser-only services cost $2-$3 at 5% but give only the browser. Cloud desktops cost $22-$65 (AWS) or a flat $28 (W365, Windows). Platform fees to remember: E2B Pro $150/mo, Vercel Pro $20/mo, Steel Scale $250/mo, Runloop Pro $250/mo, Modal Team $250/mo, Koyeb Pro $29/mo, Cloudflare Workers Paid $5/mo, Google Workstations $144/mo per cluster. None of these figures include the LLM cost, which for computer-use screenshot loops will likely dominate the whole unit cost (not researched here).

---

## 4. Sources (all accessed 2026-10-28)

Official pricing/docs pages:
- Fly.io pricing https://docs.fly.io/about/pricing ; regions https://docs.fly.io/reference/regions ; suspend https://docs.fly.io/reference/suspend-resume ; Sprites https://fly.io/sprites and https://docs.fly.io/sprites
- E2B pricing https://e2b.dev/pricing ; persistence https://docs.e2b.dev/sandbox/persistence
- Daytona pricing https://www.daytona.io/pricing ; limits https://www.daytona.io/docs/en/limits ; computer use https://www.daytona.io/docs/en/computer-use ; DPA https://www.daytona.io/dpa ; ToS https://daytona.io/terms-of-service
- Modal pricing https://modal.com/pricing ; sandbox guide https://modal.com/docs/guide/sandbox ; regions https://modal.com/docs/guide/region-selection ; DPA https://modal.com/legal/dpa
- Vercel Sandbox pricing https://vercel.com/docs/sandbox/pricing (fetched via /docs/vercel-sandbox/pricing) ; regions https://vercel.com/docs/sandbox/concepts/regions ; DPA https://vercel.com/legal/dpa
- Runloop https://www.runloop.ai/pricing ; Blaxel https://blaxel.ai/pricing (fetched via blaxel.ai/pricing result) 
- Cloudflare Containers https://developers.cloudflare.com/containers/pricing/ and /containers/platform-details/limits/ ; Browser Rendering https://developers.cloudflare.com/browser-rendering/pricing/ and /limits/
- AWS Fargate https://aws.amazon.com/fargate/pricing/ ; AgentCore pricing https://aws.amazon.com/bedrock/agentcore/pricing/ ; AgentCore regions https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html ; WorkSpaces for AI agents https://aws.amazon.com/workspaces/ai-agents/faqs/ ; streaming pricing https://aws.amazon.com/workspaces/applications/pricing/ ; AppStream https://aws.amazon.com/appstream2/pricing/ ; WorkSpaces https://aws.amazon.com/workspaces-family/workspaces/pricing/
- Azure Container Apps https://azure.microsoft.com/en-us/pricing/details/container-apps/ ; ACI https://azure.microsoft.com/en-us/pricing/details/container-instances/
- Google Cloud Run https://cloud.google.com/run/pricing (page truncated/JS) ; Workstations https://cloud.google.com/workstations/pricing (JS; figures from search snippet) ; GKE https://cloud.google.com/kubernetes-engine/pricing (JS; snippet)
- Railway https://railway.com/pricing ; Render https://render.com/docs/disks and https://render.com/docs/regions ; Koyeb https://koyeb.com/pricing ; Northflank https://northflank.com/pricing ; DigitalOcean App Platform https://www.digitalocean.com/pricing/app-platform ; Heroku https://www.heroku.com/pricing ; Scaleway https://www.scaleway.com/en/pricing/serverless/ (403 to fetch; figures from search snippet) ; OVHcloud AI Deploy https://docs.ovhcloud.com/en/guides/public-cloud/ai-machine-learning/ai-deploy-billing
- Browserbase https://www.browserbase.com/pricing and https://docs.browserbase.com/features/contexts ; Steel https://www.steel.dev/pricing ; Browserless https://www.browserless.io/pricing ; Anchor https://www.anchorbrowser.io/pricing and https://docs.anchorbrowser.io/essentials/managed-authentication ; Hyperbrowser https://www.hyperbrowser.ai/pricing and https://docs.hyperbrowser.ai/sessions/profiles ; Kernel https://www.kernel.sh/docs/info/pricing ; Apify https://apify.com/pricing
- Windows 365 https://www.microsoft.com/en-us/windows-365/all-pricing ; Paperspace https://www.paperspace.com/pricing ; Kasm https://trust.kasmweb.com/resources/features-matrix and https://kasm.com/docs/1.16.0/license.html ; Guacamole https://guacamole.apache.org/
- OpenAI computer use https://developers.openai.com/api/docs/guides/tools-computer-use ; Anthropic computer use https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool (seen only in search results)
- Fly DPA info: https://community.fly.io/t/gdpr-and-dpas-we-can-help/4073 and https://fly.io/compliance.md (search results)

Third-party (unverified): Cloud Run Tier 2 rates (dev.to / HAMY via search), ACI East-US rates, AVD D4as v5 price, Render $7/$25/$0.25 (devtune.ai/trustradius), Replit and CodeSandbox plan prices, OpenAI hosted sandbox $0.03-$0.48 per 20 min (apidog roundup), Kasm older per-user prices.

---

## 5. NOT FOUND / needs owner decision

Prices not on any page I could read: Cloud Run EU/Milan rates; GKE Autopilot Milan rates; Fargate EU/Milan rates; Azure Container Apps and ACI rates (both pages show "$-"); Azure Dynamic Sessions rate; Render official prices; Koyeb CPU instance and volume prices; OVH AI Deploy official rate and managed K8s; Scaleway official page (403); CodeSandbox SDK; Morph; Bright Data Browser API; Shadow PC; Google Workstations VM rates; AVD per-VM rates; WorkSpaces Personal EU prices; AppStream/WorkSpaces EU instance rates; Vercel Sandbox EU regional rates; Fly EU machine rates; Gemini computer use; AWS App Runner.
Capabilities/limits not found: Sprites regions, per-Sprite size and GUI; Northflank EU region and pause billing; Railway region and sleep; Cloudflare Containers durable disk; Browserbase paid session max; Kernel concurrency/profiles; Hyperbrowser max session/regions; Anchor max_duration; AgentCore Browser quotas; E2B paused-sandbox cost; Daytona per-sandbox max and auto-stop defaults; Runloop and Blaxel regions.
Legal: DPA links verified only for Daytona, Modal, Vercel and a Fly process description. DPA/ToS/AUP position on automation, scraping or arbitrary code was NOT FOUND for E2B, Browserbase, Steel, Kernel, Cloudflare, AWS, Google, Azure, Railway, Northflank, Koyeb, Render, Runloop, Blaxel, others. Owner decision needed: whether any provider's AUP allows running logged-in browsing/automation on customers' behalf (e.g. against third-party sites with CAPTCHAs/ToS bans) - nobody's AUP was read.
Maturity (founded, funding): NOT sourced for any provider.
Owner decisions: acceptable EU region set (several EU options exist but no Milan for Fly/Vercel), whether suspend with memory is needed, whether GUI automation must be headed, and which of the unverified "pause bills zero compute" claims (Northflank) to test.

## 6. Limits of this research

- Single-day desk research with a small-model page summariser; WebFetch returns model-written summaries, not raw page text, so a number can be mis-extracted (e.g. Daytona and AWS pages contain internal inconsistencies I flagged). Every figure should be re-checked on the vendor page before use.
- Many hyperscaler pricing pages are JavaScript-rendered; I did not use their pricing APIs or calculators (forbidden: no API calls), so regional EU prices for Google, Azure and AWS are missing.
- I did not test anything: no sign-up, no sandbox run, no Chromium/Xvfb check. All "FULL (inferred)" labels are technical inference from "it is a Linux VM/container", not vendor claims.
- Cost model uses my assumed PC size (1 vCPU/2 GB/10 GB), US/default prices where EU was not found, 720 h months, 25% active CPU for Vercel, Sprites sized 1 CPU/2 GB (not documented), and rootfs 2 GB for Fly. It excludes egress, proxies, CAPTCHA, support plans, VAT, LLM tokens and engineering/ops effort.
- Isolation labels (Firecracker, gVisor) come partly from general knowledge, not from the pages fetched; HQ/jurisdiction was not verified from sources; funding/founded not researched.
- Coverage is broad but not exhaustive: Together/other code-interpreter sandboxes, Gemini computer use, App Runner, noVNC self-hosting notes, EKS/AKS, and Kata specifics were not researched in depth; search was US-only and some results were stale or conflicting.

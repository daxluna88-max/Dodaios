# Cloud providers, category R1: infrastructure as a VM (VPS / cloud VM / bare metal / microVM layers)

Date of research: 2026-10-28 (all "accessed" dates below are 2026-10-28).
Branch: `claude/cloud-r1`. Scope: public web research only. No purchases, sign-ups, API calls, downloads or code runs were made.
This is research, NOT a recommendation. No provider or architecture is chosen here.

## 0. How to read this document

Target workload (from owner brief): per-customer "physical PC"-like assistant: files and folders, a real headed/virtual-display Chromium with persistent profile, shell and GUI automation, long tasks, persistent storage. Customers mostly idle. Strong isolation (VM or microVM). EU/GDPR preferred. Customer is Italian.

**Reference spec used throughout ("S")**: 2 vCPU, 4 GB RAM, at least 40 GB SSD/NVMe, Linux. This is my assumption for "Chromium + Python agent", not a measured figure. Chromium with several tabs can exceed 4 GB; 8 GB may be needed. Larger plans are listed where they were the only documented option.

**Price conventions**: currency and VAT status are quoted exactly as the source shows them. "ex VAT" means the page said the price excludes VAT. Italian VAT is 22 percent and would be added for a consumer (B2C) customer; I did not convert prices. A number tagged **(unverified, third party)** comes from an aggregator or review site and must not be used for a decision without checking the provider.

**Source reliability tags used in the text**
- `[direct]` = I fetched the provider's own page and read the figure.
- `[search-extract]` = the figure came from a web-search extract that quotes the provider's page; the direct fetch returned 403/404/JS-only. Treat as "probably right, re-verify".
- `[3rd]` = third party, unverified.
- `[bg]` = my background knowledge, NOT verified in this session. Used only for non-price facts (HQ, API existence). Re-verify before relying.

**Important context found during research**: Hetzner raised cloud prices twice in 2026 (1 April, then 15 June), and the Cost-Optimized (CX/CAX) line is shown "Currently unavailable" on the live page. OVHcloud raised several dedicated lines in H2 2026. Prices in older blog posts and AI-generated summaries are stale. Equinix Metal was shut down on 30 June 2026. Oracle halved the Always Free Ampere allowance in 2026.

---

## 1. Summary comparison table

Columns: price is for the closest documented plan to S unless noted. "Stopped billed?" = do you keep paying compute when the VM is powered off. "Nested KVM" = can you run Firecracker/KVM inside the VM (needed for microVM-per-customer on a rented VM).

| Provider | HQ / EU or IT DC | S-class plan and documented price | Billing granularity | Stopped billed? | Nested KVM | Per-customer API | Source tag |
|---|---|---|---|---|---|---|---|
| Hetzner Cloud | DE / DE (FSN, NBG), FI (HEL); no IT | CPX22 2vCPU/4GB: EUR 19.49/mo, 0.0312/h ex VAT ex IPv4. CX23 2/4/40: EUR 5.49/mo (docs) or 5.99 (product page), "Currently unavailable" | hourly, capped at monthly | YES, billed in any state until deleted | NO ("not possible on cloud server") | yes (REST, CLI) | direct |
| Hetzner dedicated AX | DE / DE, FI | AX41 6c/64GB/2x512GB NVMe EUR 59/mo, setup 0; AX42 8c/64GB EUR 79-99, setup 49; AX102 16c/128GB EUR 159-259, setup 129 (ex VAT) | monthly | n/a | yes (own KVM host) | Robot webservice (bg) | direct |
| Hetzner server auction | DE / DE, FI | page says "from EUR 72.70/mo ex VAT", no setup fee; individual server list not readable (JS) | monthly | n/a | yes | Robot | direct (partial) |
| OVHcloud VPS | FR / FR, DE, PL, others; Italy NOT FOUND | VPS-1 2vCPU/4GB/40GB NVMe: EUR 3.81/mo ex VAT (4.65 incl.) on 12-month upfront; VPS-2 4/8/75: 7.21 ex VAT | monthly (commitment) | not applicable (monthly) | NOT FOUND | yes (page: customer area and API) | search-extract (it page via fetch) |
| OVHcloud dedicated | FR | RISE-L 16c/128GB USD 177 (US page); EUR page not read; Advance-1 Gen2026 EUR 204.99 current / 371.99 new order | monthly | n/a | yes | yes (bg) | direct, USD |
| Scaleway | FR / FR (Paris), NL (Amsterdam), PL (Warsaw); Italy NOT FOUND | DEV1-M EUR 14.74/mo, 0.0202/h (spec 3vCPU/4GB is bg) | hourly (60-min minimum) | NOT CONFIRMED officially | "works" per third party only | yes (bg) | search-extract |
| Infomaniak | CH / CH (Geneva area) | VPS Lite 2vCPU/4GB/60GB: CHF 7.20/mo, annual commitment | monthly, annual | n/a | NOT FOUND | OpenStack public cloud API (bg) | search-extract |
| Aruba Cloud | IT / IT (Arezzo, Ponte San Pietro, bg), CZ, FR, DE, UK, PL | Cloud VPS O2A4 2vCPU/4GB/40GB: EUR 6.29 + VAT/mo | monthly | NOT FOUND | NOT FOUND | yes (bg) | search-extract |
| IONOS | DE / DE, ES, UK, US | VPS M+ 2vCPU/4GB/120GB: EUR 4/mo ex VAT (22% IT VAT) for first 3 months only; renewal price NOT FOUND | monthly | n/a | NOT FOUND | yes (bg) | direct |
| netcup | DE / Vienna, Nuremberg, Amsterdam, US, SG | VPS 500 G12.5 2vCPU/4GB/64GB: EUR 8.26 (page also shows 9.50 "incl 19% VAT"; the two do not reconcile), 12-month term | monthly | n/a | NOT FOUND | partial (bg) | direct |
| Contabo | DE / DE, others | Cloud VPS 10 (4 vCPU/8GB/75GB): about EUR 4.40-4.50/mo **(unverified, third party)**; official page 403 | monthly | n/a | NOT FOUND | yes (bg) | 3rd |
| Hostinger | LT / 10 DCs (EU list not shown) | KVM 2 (2 vCPU/8GB/100GB): USD 8.79/mo promo, USD 14.99 renewal, 24-month upfront | monthly | n/a | NOT FOUND | public API (page) | direct, USD |
| Strato | DE / DE, ES, FR (page) | VPS M 2vCPU/4GB/120GB: EUR 10/mo incl. VAT (German page, promo EUR 3 for first month) | monthly (1/6/12 mo) | n/a | NOT FOUND | NOT FOUND | direct |
| Vultr | US / many; EU incl. Frankfurt, Amsterdam, Paris (bg) | 2vCPU/4GB regular about USD 20/mo, 0.0274/h **(unverified, third party)**; official page 403 | hourly | reported YES (3rd) | NOT FOUND | yes (bg) | 3rd |
| Akamai / Linode | US / Milan, Frankfurt, Paris, Amsterdam, Stockholm (bg) | Nanode 1GB USD 5/mo (0.0075/h); 4 GB shared plan price NOT retrieved; G8 dedicated 2vCPU/4GB USD 0.07/h | hourly | YES (page says billed) | NOT FOUND | yes (bg) | direct |
| DigitalOcean | US / Frankfurt, Amsterdam, London (bg) | Basic 2vCPU/4GB/80GB: USD 24/mo, 0.03571/h | per second (min 60 s or USD 0.01) | YES | NOT FOUND | yes (bg) | direct |
| UpCloud | FI / Helsinki, Frankfurt, Amsterdam, Warsaw, Madrid, others (bg) | GP 2CPU/4GB: EUR 30.80/mo global (32.40 Helsinki) per a dated notice; or EUR 26 / 0.0387 per h in older table **(conflicting, unverified)** | hourly (28-day cap) | NOT FOUND | reported supported (3rd) | yes (bg) | 3rd/conflicting |
| Exoscale | CH / AT (Vienna), DE, CH, BG | Small 2vCPU/2GB, Medium 2vCPU/4GB: hourly price NOT FOUND (template placeholders) | hourly | partial: stopped VM still pays local SSD and licenses (direct) | NOT FOUND | yes (bg) | direct (no price) |
| AWS EC2 | US / eu-south-1 Milan, eu-central-1 Frankfurt, others | t4g.medium (ARM, 2/4) USD 0.0384/h Milan and Frankfurt **(unverified, third party)**; t3.medium Milan/Frankfurt NOT FOUND | per second (bg) | compute free when stopped (bg), EBS billed | YES on C8i/M8i/R8i since Feb 2026; also metal | yes | 3rd |
| AWS Lightsail | US / EU regions (bg) | 2vCPU/4GB/80GB: USD 24/mo; snapshot USD 0.05/GB-mo | monthly bundle | NOT STATED | NOT FOUND | yes (bg) | direct |
| Google Compute Engine | US / europe-west8 Milan, europe-west3 Frankfurt | e2-medium (2 vCPU shared/4GB) Milan USD 0.0389-0.0642/h, Frankfurt 0.0432-0.0713/h **(sources disagree, unverified)**; official page unreadable | per second (bg) | compute free, disks and static IP billed (page-independent general knowledge, bg) | yes, nested virt documented (bg) | yes | 3rd |
| Azure | US / Italy North (Milan), West Europe, others | B2als_v2 (2 vCPU/4GB) from USD 0.0246/h **(unverified, third party)**; B2s v1 USD 0.0480/h Linux West Europe and Italy North (3rd) | per second (bg) | compute free if deallocated (bg) | yes on Dv3-family (bg) | yes | 3rd |
| Oracle Cloud (OCI) | US / Milan, Frankfurt, others (bg) | A1.Flex USD 0.013106 per OCPU-h + 0.0019659 per GB-h (search-extract of price list); Always Free now 2 OCPU/12 GB | hourly | NOT FOUND | NOT FOUND | yes | search-extract |
| IBM Cloud VPC | US / Frankfurt, Madrid, London (bg) | bx2-2x8 (2 vCPU/8GB) about USD 0.096-0.10/h (3rd, region unspecified) | hourly | NOT FOUND | NOT FOUND | yes | 3rd |
| Alibaba Cloud | CN / Frankfurt, London | ecs.t6 2/4 about USD 20.88/mo **(unverified, third party, region unspecified)** | hourly/monthly | NOT FOUND | NOT FOUND | yes | 3rd |
| Tencent Cloud | CN / Frankfurt (Lighthouse/CVM) | overage traffic USD 0.077/GB in Frankfurt; bundle price NOT FOUND | monthly bundles | NOT FOUND | NOT FOUND | yes | direct (doc) |
| Seeweb | IT / Milan, Frosinone (+ CH, BG) | Shared CPU "4G" 4vCPU/4GB: EUR 0.048/h on demand (0.038 with 12-month reserve); "2G" 2vCPU/2GB EUR 0.032/h; VAT not stated | hourly | NOT FOUND | NOT FOUND | yes (bg) | direct |
| Equinix Metal | US | Shut down 30 June 2026. Not applicable | n/a | n/a | n/a | n/a | search-extract |
| OpenMetal | US / Amsterdam, Ashburn, LA, SG | Medium v4 (2x Xeon Silver 4510, 256 GB): official "contact for pricing"; USD 572.76/mo **(unverified, third party)** | monthly | n/a | yes (bare metal) | yes (OpenStack) | 3rd |
| latitude.sh | BR/US / Frankfurt and others | NOT FOUND (only aggregator figures with conflicting numbers) | hourly | n/a | yes | yes | NOT FOUND |
| Register.it | IT | VPS 4 vCPU/8GB EUR 41.30 ex VAT **(unverified, third party)**; DC location conflicting | monthly | n/a | NOT FOUND | NOT FOUND | 3rd |
| TIM (Cloud Flex) | IT | VPS 2 vCPU/4GB EUR 42.92/mo, VAT status unclear **(search-extract)** | monthly | n/a | NOT FOUND | NOT FOUND | search-extract |
| Fastweb / Vodafone | IT | NOT FOUND | | | | | NOT FOUND |
| IBM, Apple/Mac clouds | | Apple/Mac cloud: not applicable (macOS VMs cannot run the Linux/Chromium/Xvfb stack; Apple licence limits virtual macOS to Apple hardware, 24 h minimum on AWS Mac) [bg] | | | | | |

---

## 2. Per-provider detail

Format: HQ/DC; products; smallest viable spec; price (with source); billing; stopped billing; snapshots/volumes; traffic; API; nested virt; GPU; DPA; AUP on automation; reliability; caveats.

### 2.1 Hetzner (DE)

**Hetzner Cloud** (hetzner.com/cloud, docs.hetzner.com)
- DC: Falkenstein, Nuremberg (DE), Helsinki (FI), Ashburn, Hillsboro (US), Singapore. No Italian DC. [direct]
- Plans documented in the official price-adjustment page (effective 15 June 2026, page last changed 2026-07-08, EUR ex VAT, ex IPv4), Germany/Finland: CX23 5.49/mo (0.0088/h); CX33 8.49 (0.0136); CX43 15.99 (0.0256); CAX11 5.99 (0.0096); CAX21 10.49 (0.0168); CPX22 19.49 (0.0312); CPX32 35.49 (0.0569); CCX13 42.99 (0.0689). [direct, https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/]
- Specs from the product page [direct]: CX23 2 vCPU / 4 GB / 40 GB NVMe; CX33 4/8/80; CAX11 2 (Ampere ARM)/4/40; CPX plans are Regular Performance (AMD), entry CPX02 1 vCPU/1 GB/20 GB EUR 6.49. The CPX22 spec (2 vCPU / 4 GB) is [bg], not read on a page this session.
- **Availability problem**: the live Cost-Optimized page (CX/CAX) lists all plans as "Currently unavailable" and shows slightly higher prices (CX23 EUR 5.99, CX33 8.99, CAX11 6.49 "incl. IPv4"). Regular Performance (CPX) is orderable. Third parties report CPX22 at EUR 19.99 in October (unverified, third party). Price figures differ between the docs page and the product page: both are quoted here.
- Billing: hourly, capped at the monthly price. EU plans include 20 TB traffic; overage EUR 1.00/TB (the page text says "per TB per hour", likely a page typo; treat as per TB). [direct]
- **Stopped servers are billed**: "All servers that have finished their creation process will be billed until they are deleted, regardless of their state." [direct, cost-optimized page]. To stop paying you must delete the server; Hetzner snapshots cost EUR 0.0143/GB/month ex VAT [direct]. Backups: daily, 7 slots, "from about EUR 1.20/month" [direct, regular-performance page]. Volume and primary IPv4 prices: NOT FOUND (page summary did not show them).
- Nested virtualisation: not supported on cloud servers ("No, this is not possible on cloud server") [direct, docs.hetzner.com/cloud/servers/faq/]. So Firecracker inside a Hetzner Cloud VM is not possible.
- API: REST API, CLI, Go/Python libs, Terraform [direct].
- GPU: none on Cloud (GPU is in dedicated GEX line, bg, not priced here).
- GDPR/DPA: Terms section 6: processing under GDPR; a processing-order contract "is not concluded automatically", offered through the customer account, with EU SCCs for third-country products [direct, https://www.hetzner.com/legal/terms-and-conditions/]. Certifications on product page: ISO 27001 (DE and FI parks), C5 [direct].
- AUP: Terms prohibit unlawful/porn/extremist/gambling content (8.2), spam, crypto mining (8.3), DDoS and open mail relays (5.2); violations can cause lock and termination (2.7). Scraping, automated browsing and crawling: **not addressed in the text I read** (not found). Ports 25 and 465 are closed by default on Cloud [direct].
- Reliability: SLA 99.9 percent stated on the product page. Reviews report CPU steal on shared plans (not verified here).
- Caveat: prices rose roughly 25-140 percent in 2026 depending on plan; CPX22 went from 7.99 to 19.49.

**Hetzner dedicated (AX line)** [direct, hetzner.com/dedicated-rootserver/matrix-ax/, EUR ex VAT]
- AX41: Ryzen 5 3600, 6 cores, 64 GB DDR4, 2x512 GB NVMe, EUR 59.00/mo, setup 0.
- AX42: Ryzen 7 PRO 8700GE, 8 cores, 64 GB DDR5 ECC, 2x1.92-7.68 TB NVMe, EUR 79-99/mo, setup 49.
- AX102: Ryzen 9 7950X3D, 16 cores, 128 GB DDR5 ECC, up to 2x15.36 TB, EUR 159-259/mo, setup 129.
- AX162: EPYC 9454P, 48 cores, 128-512 GB, EUR 319-614/mo, setup 304. (Drive-to-price mapping on the page is ambiguous; ranges shown as given.)
- Monthly billing. 1 Gbit port, unlimited traffic, 99.9 percent network availability (auction page text). Hardware is plain KVM host: Firecracker/Cloud Hypervisor run natively (no nesting).
- Server auction: page header says "from EUR 72.70/mo ex VAT", "All without setup fees", unlimited traffic [direct]. The actual list is rendered by JavaScript and the JSON feed URL I guessed returned 404: individual auction prices NOT FOUND. Third party says auction prices are dynamic and rose only about 3 percent in 2026.
- DPA/AUP: same Terms as above (Robot customers).
- Caveat: no hot spare; disk/host failure means downtime unless you build replication. No included backup (Storage Box price NOT FOUND).

### 2.2 OVHcloud (FR)
- VPS "2027" range [search-extract via ovhcloud.com/it/vps, accessed 2026-10-28]: VPS-1 2 vCore/4 GB/40 GB NVMe 500 Mbps, EUR 3.81 ex VAT (4.65 incl.); VPS-2 4/8/75, 7.21 (8.80); VPS-3 6/12/100, 10.40 (12.69); VPS-4 8/24/200, 19.96 (24.35). These are prices with a 12-month upfront commitment (monthly equivalent); the monthly-no-commitment price is NOT FOUND. USD page shows VPS-1 at USD 4.54.
- Included: unlimited traffic, daily backup of previous 24 hours, anti-DDoS, KVM console, 99.9 percent SLA, management by customer area and API. Snapshots from USD 0.40/mo; "Premium automatic backup" from USD 1.40/mo (USD page).
- DC locations: "many global locations"; whether an Italian DC exists: NOT FOUND. OVH has FR, DE, PL, UK, CA etc. (bg).
- Stopped billing: monthly product, no hourly stop saving (commitment). Nested KVM on VPS: NOT FOUND.
- Dedicated: price page is USD on the US site: RISE-L (Ryzen 9 9950X, 16c, 128 GB, 2x960 GB NVMe) USD 177/mo + 177 setup, flagged "soon available"; Game-1 from USD 331; Advance-5 from USD 467. EUR example totals from OVH blog (2026-08): ADVANCE-1 Gen 2026, 128 GB RAM, 4x960 GB NVMe EUR 204.99 (current), 289.49 at renewal, 371.99 new order; Rise and Kimsufi are not affected by the H2 2026 rise [direct, blog.ovhcloud.com]. Kimsufi/Eco entry price NOT FOUND.
- Public Cloud instances (hourly): not researched, NOT FOUND.
- DPA/AUP: not read this session.

### 2.3 Scaleway (FR)
- Locations: Paris, Amsterdam, Warsaw (bg). Italy: NOT FOUND.
- Prices from the official virtual-instances page as reported by a search extract (direct fetch gave 403): DEV1-S EUR 6.55/mo; DEV1-M EUR 14.74/mo (0.0202/h); PLAY2-MICRO EUR 40.20/mo; STARDUST1-S 0.43/mo (0.0006/h) [search-extract]. Third-party aggregators give conflicting PRO2/PLAY2 numbers (PRO2-XXS 40.15 to 40.95; PLAY2-PICO 10.42). I did not verify the DEV1-M vCPU/RAM (3 vCPU/4 GB is [bg]) and whether DEV1 is still sold in Oct 2026.
- Billing: hourly, 60-minute minimum per resource (billing FAQ extract). "Uptime includes standby time", so standby is billed as running. Whether a powered-off instance with volumes costs only storage: NOT confirmed officially; third party says storage and IP continue at about EUR 0.002/h each (unverified, third party).
- Storage: Block Snapshot EUR 0.000044/GB/h (about EUR 0.032/GB/mo at 730 h, my arithmetic); Block Storage 5K EUR 0.000118/GB/h (about EUR 0.086/GB/mo); a Scaleway blog says Block Storage rises to 0.00013/GB/h and Compute local storage to 0.000049/GB/h from 1 June 2026 [search-extract].
- Nested KVM: "works" per one Hacker News comment (unverified, third party).
- Elastic Metal (bare metal): page 403, prices NOT FOUND.
- API: yes (bg). DPA/AUP: not read.

### 2.4 Infomaniak (CH)
- HQ Geneva, DCs in Switzerland (bg). Switzerland is covered by an EU adequacy decision (bg), not EU/EEA.
- VPS Lite [search-extract of the official page]: 1 vCPU/2 GB/20 GB CHF 2.70/mo; 2 vCPU/4 GB/60 GB CHF 7.20/mo; 4 vCPU/4 GB/80 GB CHF 9.00/mo; annual commitment paid in a single payment for 12 months (monthly equivalent). The page title shows "from EUR 2.70/month" in EUR on the EN page. VAT status not read.
- Public Cloud (OpenStack) hourly price: NOT FOUND (one review gives CHF 16.10/mo for 4 vCPU/8 GB/50 GB plus 1 TB object storage: unverified, third party).
- Nested KVM, snapshots, DPA, AUP: NOT FOUND.

### 2.5 Aruba Cloud (IT)
- HQ Italy; DCs in Italy (Arezzo, Ponte San Pietro [bg]), plus Czech Republic, France, Germany, UK, Poland (older third-party list).
- Cloud VPS (OpenStack-based) O2A4: 2 vCPU/4 GB/40 GB, 25 TB monthly traffic, EUR 6.29 + VAT per month; page start price EUR 2.49 + VAT [search-extract of arubacloud.com/vps, accessed 2026-10-28]. A 4 vCPU/8 GB/80 GB plan (O4A8) is EUR 11.79 ex VAT per a third-party comparison (unverified, third party). "Cloud Server" (the older hourly/VDC product) CS O2A4 about EUR 22.63/mo (unverified, third party); VDC page says add EUR 3.65 + VAT per public IPv4.
- Stopped billing, snapshots, nested KVM, DPA, AUP: NOT FOUND.
- Direct fetches of arubacloud.com pricelist returned 404.

### 2.6 IONOS (DE)
- VPS Linux (Italian page, EUR ex 22% VAT) [direct]: S+ 1/2/60 EUR 2; M+ 2/4/120 EUR 4; L+ 4/8/240 EUR 7; XL+ 8/16/480 EUR 12; XXL+ 12/24/720 EUR 17. These are promo prices for the first 3 months, setup fee EUR 10, 30-day trial; **post-promo and minimum term NOT FOUND**. The US page shows "USD 5/month after 3 months with a 1-year term" for the same M+ (USD), which suggests a post-promo level near that. Treat as unverified.
- DC: DE, ES, UK, US. Unlimited traffic, 1 Gbit/s. Extra IPv4 EUR 5/mo ex VAT. Cloud backup from USD 0.065/GB/mo (US page).
- IONOS Cloud (Compute Engine / Cloud Cubes) pricing page: 404, NOT FOUND.
- Nested KVM, API for VPS, DPA, AUP: NOT FOUND (IONOS Cloud has an API, bg).

### 2.7 netcup (DE)
- VPS G12.5 x86, 12-month term, EUR per month [direct]: VPS 500 2 vCore/4 GB/64 GB 8.26 (listed 9.50 "incl. 19% VAT"); VPS 1000 4/8/128 14.50 (16.68); VPS 2000 8/16/256 26.92; VPS 4000 12/32/512 45.36. The page notes 1/12/24-month terms and about 13 percent (12 mo) / 26 percent (24 mo) discounts. The net and gross figures are not consistent (about 15 percent apart, not 19), so confirm in checkout.
- DC: Vienna, Nuremberg, Amsterdam, Manassas (US), Singapore; location fixed after order.
- API: a Server Control Panel API exists (bg); nested KVM: NOT FOUND; DPA, AUP: not read.

### 2.8 Contabo (DE)
- Official page returns 403. Aggregators list Cloud VPS 10 at about EUR 4.40-4.50/mo, with 4 vCPU/8 GB/75 GB NVMe and 200 Mbit port (unverified, third party; one source says 3 vCPU; older snapshot EUR 5.36). Private networking EUR 2.29/VPS (unverified, third party). Traffic claims conflict (unmetered vs 32 TB).
- Contabo is known for oversubscribed CPU/IO (bg, community reputation; not verified here).
- Everything else NOT FOUND.

### 2.9 Hostinger (LT)
- KVM plans, 24 months upfront, USD [direct, hostinger.com/vps-hosting accessed from a US-geo fetch]: KVM 1 1 vCPU/4 GB/50 GB USD 6.49 (renewal 11.99); KVM 2 2/8/100 USD 8.79 (renewal 14.99); KVM 4 4/16/200 USD 12.99 (28.99); KVM 8 8/32/400 USD 25.99 (49.99). 30-day money-back, "Cancel anytime", public API listed. 10 DCs "across 4 continents", EU locations not named on the page. EUR/VAT page not read. Weekly free backups. Nested KVM, snapshots, DPA, AUP: NOT FOUND.

### 2.10 Strato (DE)
- VPS Linux [direct, strato.de, all prices incl. VAT]: S 1/2 GB/60 GB EUR 4 (promo 2 for one month); M 2 vCore/4 GB/120 GB EUR 10 (promo 3 for one month); L 4/8/240 EUR 20; XL 8/16/480 EUR 39; XXL 12/24/720 EUR 56. Setup EUR 9; terms 1/6/12 months; unlimited traffic; no backup. Location chosen at order; page names Spain and France as European options, Germany not explicitly. API, nested KVM, DPA, AUP: NOT FOUND.

### 2.11 Vultr (US)
- Official fetch failed (403) and the indexed official page is old. Aggregators: Regular vc2-2c-4gb USD 20/mo (0.0274/h); High Frequency vhf-2c-4gb USD 24.02 (0.0329/h) (unverified, third party). Third parties say powering off keeps charges; snapshots USD 0.05/GB-mo (unverified, third party). Bare metal prices: NOT FOUND. Hourly billing, API yes (bg). Nested KVM, DPA, AUP: NOT FOUND.

### 2.12 Akamai / Linode (US)
- Official page [direct, akamai.com/cloud/pricing and /pricing/cpu]: Nanode 1 GB USD 5/mo (0.0075/h); Dedicated G8 smallest (2 vCPU, 4 GB) USD 0.07/h; G7 dedicated from USD 43/mo (4 GB); G6 from USD 36/mo (4 GB); High Memory Linode 24 GB USD 60/mo. Egress overage USD 0.005/GB; G8 has no bundled egress. **Powered-off VMs are billed** ("We maintain your saved data and reserve your ability to use other resources"). Backups price: NOT FOUND. 4 GB shared plan: NOT retrieved.
- Regions incl. Milan (bg). Nested virtualisation: NOT FOUND (a community thread exists, not read).

### 2.13 DigitalOcean (US)
- Basic Droplets [direct, digitalocean.com/pricing/droplets]: 2 vCPU/4 GiB/80 GiB USD 24/mo (0.03571/h), 4000 GiB transfer; 1 vCPU/1 GiB USD 6; 1 vCPU/512 MiB USD 4. Snapshots USD 0.06/GB/mo.
- Billed per second (min 60 s or USD 0.01); powered-off droplets are still billed, only destroy stops charges; bundled plans capped at 672 h/month [direct, docs.digitalocean.com]. Backup and reserved-IP prices: NOT FOUND.
- Nested KVM: a decade-old forum post says supported (unverified, third party). DPA/AUP: not read.

### 2.14 UpCloud (FI)
- Official page 403. Search extracts conflict: a dated notice shows GP 2 CPU/4 GB at EUR 30.80/mo global (32.40 Helsinki); an older table shows EUR 26/mo (0.0387/h) global, 30 (0.0446/h) Helsinki. Hourly billing, 28-day monthly cap. Stopped billing, backup price, snapshots: NOT FOUND. Nested KVM reported as available by a support chat (unverified, third party). Datacentres incl. Helsinki, Frankfurt, Amsterdam, Warsaw, Madrid [bg].

### 2.15 Exoscale (CH)
- Pricing page [direct]: Standard instances Micro 1 vCPU/512 MB, Tiny 1/1 GB, Small 2/2 GB, Medium 2/4 GB, local SSD 10-400 GB. Hourly prices were unrendered placeholders in the fetched text: **NOT FOUND**. Prices exclude tax. A stopped VM still pays for local storage and software licences. Snapshots and block storage billed per GiB.hour (price NOT FOUND). Egress: free 1.42 GiB/hour per instance, then per GiB. Zones include Vienna, Frankfurt, Zurich, Geneva, Munich, Sofia (bg; page did not name them). DPA and AUP: not read.

### 2.16 Amazon Web Services (US)
- EC2 official on-demand page did not contain per-instance prices (only CPU credit surcharge: T3 USD 0.05 and T4g USD 0.04 per vCPU-hour in unlimited mode) [direct]. Aggregators: t4g.medium USD 0.0384/h in Frankfurt and Milan; t3.medium us-east-1 about USD 0.0416/h; Milan/Frankfurt t3.medium NOT FOUND (all unverified, third party).
- **Nested virtualisation**: since 12 February 2026 available on non-metal C8i, M8i, R8i (Intel Xeon 6) via a CPU option, KVM and Hyper-V supported (InfoQ and AWS community reports, [3rd]); metal instances always supported. Firecracker is a documented use case on bare-metal EC2 and the 8th-gen instances need host kernel 6.1 or 6.18 [direct, Firecracker README]. Prices of C8i/M8i: NOT FOUND (larger and costlier than burstable).
- Lightsail [direct, aws.amazon.com/lightsail/pricing/]: USD 5 (0.5 GB/2 vCPU/20 GB/1 TB), 7 (1 GB), 12 (2 GB/2 vCPU/60 GB/3 TB), **24 (4 GB/2 vCPU/80 GB/4 TB)**, 44 (8 GB), 84 (16 GB/4 vCPU); snapshots USD 0.05/GB/month. Stopped-instance billing: not stated on the page.
- Spot: not priced here (NOT FOUND officially). Graviton: t4g above.
- Regions: Milan (eu-south-1) [search-extract], Frankfurt, Paris, Stockholm, Ireland, Spain (bg).
- GPU instances exist (not priced).
- DPA/AUP: not read; AWS is a US company, so US CLOUD Act exposure applies even for EU regions (general fact, bg). The AWS European Sovereign Cloud exists but was not researched.

### 2.17 Google Cloud Compute Engine (US)
- Official VM pricing page content was not readable. Aggregators for e2-medium (2 shared vCPU/4 GB): Milan USD 0.0389, 0.064 or 0.0642/h; Frankfurt USD 0.0432, 0.071, 0.0713/h; Spot roughly 0.02-0.033 (Milan) and 0.026-0.043 (Frankfurt) (all unverified, third party, contradictory).
- Stopped VM: no vCPU/RAM charge, persistent disks/IPs still charged; nested virtualisation supported on Intel/AMD VMs (both bg, not confirmed on a page this session).
- Regions Milan (europe-west8) and Frankfurt (europe-west3) confirmed in listings. DPA/AUP: not read.

### 2.18 Microsoft Azure (US)
- Third-party price aggregators (Azure Hub updated 2026-10-27; AzureSpeed): B2ats_v2 (2 vCPU, 1 GiB) USD 0.0108/h West Europe and Italy North; B2als_v2 (2 vCPU/4 GB) from USD 0.0246/h; B2s v1 (2/4) USD 0.0480/h Linux in Italy North and West Europe (all unverified, third party). Spot found only for B2ts_v2 in East US (USD 0.00936/h). Deallocated VMs do not bill compute but disks do (bg). Nested virtualisation on Dv3/Ev3 and later (bg; B-series: NOT FOUND). Italy North region exists (listing). DPA/AUP: not read.

### 2.19 Oracle Cloud Infrastructure
- Official docs [direct]: Always Free = 2 AMD micro VMs (1/8 OCPU, 1 GB) + A1.Flex first 1,500 OCPU-hours and 9,000 GB-hours/month (equivalent to **2 OCPU / 12 GB**, reduced from 4/24 in mid-2026, enforcement reported from 18 Aug 2026 [3rd]); 200 GB boot+block storage; home region only; idle reclaim if 95th-percentile CPU, network (and memory on A1) under 20 percent over 7 days.
- Paid price list (search extract of oracle.com/cloud/price-list): A1.Flex USD 0.013106 per OCPU-hour + USD 0.0019659 per GB-hour; E4.Flex USD 0.032765 per OCPU-hour. 2 OCPU/4 GB A1 = 2x0.013106 + 4x0.0019659 = USD 0.03408/h. [search-extract]
- Whether Always Free is permitted for a commercial multi-tenant service: Oracle terms not read; an idle-reclaim rule plus the 7-day idle clause makes it a poor fit for bursty customers. Milan and Frankfurt regions exist (bg). Stopped billing, nested KVM, DPA, AUP: NOT FOUND.

### 2.20 IBM Cloud VPC
- Official page requires an estimator; aggregators: bx2-2x8 (2 vCPU/8 GB) USD 0.096-0.10/h in Dallas, USD 65.04/mo (unverified, third party). Frankfurt/Madrid/Milan prices NOT FOUND. Billed per hour, per second after the first (extract). Bare Metal for VPC exists (not priced). Everything else NOT FOUND.

### 2.21 Alibaba Cloud and Tencent Cloud (CN) - jurisdiction note
- Both are Chinese-headquartered groups. EU regions (Frankfurt, London for Alibaba; Frankfurt for Tencent) exist, but the parent company is subject to PRC law (e.g. national intelligence law) [bg]. For an Italian customer's personal data under GDPR this creates a transfer/government-access question that I did not research further; no DPA text was read.
- Alibaba ecs.t6-c1m2.large (2 vCPU/4 GB) about USD 20.88/mo, region unspecified (unverified, third party). Tencent: Frankfurt overage traffic USD 0.077/GB (Tencent doc, ~16 months old); bundle price NOT FOUND.

### 2.22 Italian and other EU providers
- **Seeweb (IT)** [direct, seeweb.it Shared CPU page]: hourly on demand EUR, 10 TB traffic included, 3/6/12-month reservation discounts up to 20 percent. Plans (first table): 1G 1vCPU/1GB/20GB 0.019; 2G 2/2/40 0.032; **4G 4/4/80 0.048** (0.043 / 0.041 / 0.038 for 3/6/12 mo); 8G 4/8/160 0.063; 16G 8/16/320 0.127. A second table with roughly double prices for similar names is unexplained on the page (maybe a different CPU class or OS). VAT not stated; monthly price not shown. DC: Milan, Frosinone (Italy), also Lugano/Zurich, Sofia (page inconsistent). Stopped billing and snapshots NOT FOUND. Seeweb also sells GPU (third-party H100 USD 2.11/h, unverified, third party).
- **Register.it**: VPS 4 vCPU/8 GB EUR 41.30 ex VAT, location conflicting (NL vs IT/UK/PT/HK) (unverified, third party).
- **TIM Cloud Flex** [search-extract]: VPS 1 vCPU/2 GB EUR 30.41/mo; 2 vCPU/4 GB EUR 42.92/mo; 4 vCPU/16 GB EUR 73.75/mo; OpenStack IaaS pool from EUR 13.99/mo for 1 vCPU/2 GB; VAT status not stated. Italian cloud vouchers (MIMIT, up to 50 percent, max EUR 20k, applications from 10 November 2026) mention TIM, Retelit, Fastweb+Vodafone, WindTre as eligible operators [search-extract]. This could matter for a business customer; I did not verify eligibility rules.
- **Fastweb / Vodafone Cloud**: pricing NOT FOUND.
- **Serverplan (IT)**: appears in a third-party comparison at EUR 15/mo for 4 vCPU/8 GB (unverified, third party); not researched.
- Not researched (named in other sources but outside my verified set): Netsons, Keliweb, Cloud Europe, Retelit.

### 2.23 Bare-metal and "metal-as-a-service" providers
- **Equinix Metal**: sunset on 30 June 2026 (Equinix docs; console access ends 1 Jan 2027). Not applicable.
- **OpenMetal**: US company, DCs Ashburn, LA, Amsterdam, Singapore. Fixed monthly pricing, rate locks up to 5 years; official pages say "contact for pricing". Third party lists Medium v4 (2x Xeon Silver 4510, 256 GB DDR5, 6.4 TB NVMe) USD 572.76/mo and Large v4 (512 GB) USD 1,085.58/mo on a 1-year term, Singapore more (unverified, third party, listing dated 2026-04-18). Too large for a "cheap per customer" tier unless ~50+ customers per host.
- **latitude.sh**: bare metal, includes Frankfurt per a listing; prices NOT FOUND (aggregator numbers conflict: c3.medium USD 250/mo Dallas, USD 279 Amsterdam, unverified, third party).
- **Hetzner, OVH dedicated**: see above. **Scaleway Elastic Metal**: prices NOT FOUND (403).
- **Apple / Mac cloud** (AWS EC2 Mac, MacStadium, Scaleway Apple silicon): not applicable. macOS cannot run the Linux Chromium/Xvfb/Firecracker stack; AWS Mac instances carry a 24-hour minimum allocation and Apple's licence ties macOS VMs to Apple hardware [bg]. Would only matter if a customer needs macOS-only apps.

---

## 3. Virtualisation / isolation layers for a cheap shared host

Reality check: none of these were run. Information is from the Firecracker repo pages I read plus third-party comparisons and background knowledge. Maturity words are my judgement.

| Layer | What it is | Maturity | Browser/GUI fit | Operational burden | Needs |
|---|---|---|---|---|---|
| **Firecracker** | Minimal KVM VMM (Rust), used in production inside AWS; Apache 2.0. Releases about every 2-3 months. Snapshot/restore exists (not on the README I read; bg). | High for short-lived function/sandbox workloads | Weak by design: no GPU, no display device (no VNC on README), limited devices; Chromium works headless or with Xvfb inside the guest (bg) | High if you self-operate: you build jailer, networking (TAP), rootfs images, snapshot store, scheduler, per-tenant cgroups. Firecracker docs ask to disable SMT and KSM for multi-tenant, disable or secure swap, filter IMDS, rate-limit I/O [direct, prod-host-setup.md] | KVM on the host: bare metal or nested-capable VM |
| **Cloud Hypervisor** | Rust VMM, Firecracker's feature-richer sibling: hot-plug, VFIO passthrough, live migration (3rd) | Medium-high, used by Kata and some sandbox vendors (3rd) | Better than Firecracker for devices and GPU | High, similar DIY | KVM |
| **Kata Containers** | OCI runtime that boots each pod in a VM with QEMU/Cloud Hypervisor/Firecracker | Medium-high for Kubernetes | Works with normal container images; GPU possible via QEMU/Cloud Hypervisor | Needs Kubernetes (or containerd) plus nested/bare-metal KVM | KVM |
| **gVisor** | User-space kernel; not a VM | High (used by Modal-style platforms, 3rd) | Chromium compat is a risk: unimplemented syscalls, no kernel modules, no device access (3rd) | Medium, runs on any Linux incl. VMs without KVM | none |
| **Proxmox VE (KVM/LXC)** | Full-featured open-source virtualisation distribution with web UI, API, clustering, backups | Very high, mature | Full VMs with real display/VNC and GPU passthrough possible | Medium: well-known, but you operate a hypervisor, patching, storage and backups | bare metal |
| **LXD / Incus (KVM VMs and system containers)** | Incus is the community fork of LXD; VM and container management with REST API, snapshots, cloning, live migration | High (LXD long established; Incus actively developed; I did not check the latest release) | Full VM with virtual GPU/VNC console possible | Medium; scriptable per-customer provisioning via API | bare metal or nested |
| **Plain KVM/libvirt/QEMU** | Direct | Very high | Full | Medium-high without a manager | bare metal |

Cross-cutting points:
- Strong isolation of arbitrary code plus a browser is a hypervisor-level question, but side-channel risk remains on shared CPUs; the Firecracker guidance (disable SMT, dedicated users per VM) shows the cost of doing this correctly.
- Rented VMs that block nested virtualisation (Hetzner Cloud: confirmed no) cannot host Firecracker/KVM. Ones that probably allow it need a per-provider test: AWS 8th-gen Intel and metal (confirmed 2026), GCP and Azure (bg), Scaleway/UpCloud/DO (3rd or unverified).
- A hibernate/snapshot-to-disk model (Firecracker snapshot, or QEMU savevm, or Incus stateful stop) is a design option for idle customers; I found no price or benchmark for restore time and did not test it.

---

## 4. Cost per customer (formula, documented prices only)

### 4.1 Assumptions (all mine; change them)
- Month = 730 hours. Duty cycle d = fraction of the month the customer's VM must be running. Scenarios: idle d=5 percent (36.5 h), light d=20 percent (146 h), intense d=60 percent (438 h).
- Per-customer state = 40 GB disk/snapshot. Snapshot billed at 40 GB (provider bills by used size; I use 40 GB as a conservative upper bound).
- VM size S = 2 vCPU/4 GB where the plan exists; otherwise the nearest documented plan, noted.
- All figures ex VAT, in each provider's currency, **excluding** public IPv4, backups beyond the snapshot, LLM/API costs, support, engineering, payment fees, and egress overage.
- Idle sessions are assumed long enough that the hourly minimum (Scaleway 60 min) does not dominate. If a customer wakes the VM many times for short bursts, billing minimums change the results.
- (b) means: VM exists only while active (create or start, work, snapshot, then delete or stop). On providers that bill stopped VMs (Hetzner, DigitalOcean, Akamai), (b) must be "snapshot and delete", which is an operational pattern I assume works; I did not verify restore time or that a snapshot of a headed Chromium profile restores cleanly.

### 4.2 Architecture (a): one small VM per customer, always on
Formula: C_a = monthly list price (independent of d). Documented figures:

| Option | Price | Note |
|---|---|---|
| OVH VPS-1 2/4/40 | EUR 3.81 ex VAT | 12-month upfront; EUR 4.65 incl. VAT [search-extract] |
| Aruba VPS O2A4 2/4/40 | EUR 6.29 ex VAT | [search-extract] |
| netcup VPS 500 G12.5 2/4/64 | EUR 8.26 (page; 9.50 shown "incl. VAT") | 12-month term, discrepancy noted |
| Strato VPS M 2/4/120 | EUR 10.00 incl. German VAT | 1/6/12-month terms; setup EUR 9 |
| Infomaniak VPS Lite 2/4/60 | CHF 7.20 | annual commitment [search-extract] |
| IONOS VPS M+ 2/4/120 | EUR 4 for 3 months | renewal NOT FOUND |
| Scaleway DEV1-M | EUR 14.74 | [search-extract] |
| Hetzner CPX22 2/4/80 | EUR 19.49 ex VAT ex IPv4 | EUR 0.0312 x 730 = 22.78 > cap, so monthly cap applies |
| Hostinger KVM 2 2/8/100 | USD 8.79 promo; 14.99 renewal | 24-month upfront |
| DigitalOcean Basic 2/4/80 | USD 24 | 0.03571 x 730 = 26.07, capped at 672 h = 24.00 |
| AWS Lightsail 2/4/80 | USD 24 | |
| Seeweb 4G 4vCPU/4GB | EUR 0.048 x 730 = **35.04** | on demand, hourly |
| Seeweb 2G 2vCPU/2GB | EUR 0.032 x 730 = **23.36** | |
| TIM Cloud Flex 2/4 | EUR 42.92 | VAT unclear |

All numbers above are per customer per month and the same for the three scenarios.

### 4.3 Architecture (b): one VM per customer, off when idle, persistent disk (or snapshot)
Formula: C_b = h x P_hour + G x P_snap, with h = 730 x d, G = 40 GB.
(Stop-and-keep-disk on providers that do not bill compute when stopped would use the disk price instead of the snapshot price; no such provider has fully documented numbers in my data. Seeweb, Scaleway, Exoscale stopped-VM billing was not confirmed.)

**Hetzner CPX22 (EUR 0.0312/h, snapshot EUR 0.0143/GB-mo)** [direct]
- Snapshot: 40 x 0.0143 = 0.572 -> EUR 0.57.
- 5 percent: 36.5 x 0.0312 = 1.139; total 1.139 + 0.572 = **EUR 1.71**.
- 20 percent: 146 x 0.0312 = 4.555; total **EUR 5.13**.
- 60 percent: 438 x 0.0312 = 13.666; total **EUR 14.24** (still under the 19.49 monthly cap).
- If CX23 (EUR 0.0088/h) were orderable (currently marked "unavailable"): 5 percent 0.321+0.572 = EUR 0.89; 20 percent 1.285+0.572 = EUR 1.86; 60 percent 3.854+0.572 = EUR 4.43.

**DigitalOcean Basic 2/4 (USD 0.03571/h, snapshot USD 0.06/GB-mo)** [direct]
- Snapshot: 40 x 0.06 = USD 2.40.
- 5 percent: 36.5 x 0.03571 = 1.303; total **USD 3.70**.
- 20 percent: 146 x 0.03571 = 5.214; total **USD 7.61**.
- 60 percent: 438 x 0.03571 = 15.641; total **USD 18.04**.

**Scaleway DEV1-M (EUR 0.0202/h) + snapshot (EUR 0.000044/GB-h)** [search-extract; stopped billing unconfirmed; spec 3 vCPU is bg; snapshot price may have changed after 1 June 2026]
- Snapshot: 40 x 0.000044 x 730 = 1.285 -> EUR 1.28.
- 5 percent: 36.5 x 0.0202 = 0.737; total **EUR 2.02**.
- 20 percent: 146 x 0.0202 = 2.949; total **EUR 4.23**.
- 60 percent: 438 x 0.0202 = 8.848; total **EUR 10.13**.
- Note the instance's local storage while it exists is not included in this formula.

**Seeweb 4G (4 vCPU/4 GB, EUR 0.048/h)** [direct; storage while stopped and snapshot price NOT FOUND, so compute only]
- 5 percent: 36.5 x 0.048 = **EUR 1.75** (+ storage unknown).
- 20 percent: 146 x 0.048 = **EUR 7.01**.
- 60 percent: 438 x 0.048 = **EUR 21.02**.
- Seeweb 2G (2 vCPU/2 GB, 0.032/h) compute only: 1.17 / 4.67 / 14.02.

**Ranking of the 5 lowest documented (b) options at 20 percent duty** (arithmetic only, not a recommendation; the fifth is conditional):
1. Hetzner CX23 hypothetical EUR 1.86 (but currently "unavailable" on the live page - do not count it as orderable).
2. Scaleway DEV1-M EUR 4.23 (search-extract; stopped billing unconfirmed).
3. Hetzner CPX22 EUR 5.13 (direct; orderable; snapshot-and-delete pattern).
4. Seeweb 4G EUR 7.01 compute only (direct; storage cost unknown; bigger VM than S).
5. DigitalOcean USD 7.61 (direct; snapshot-and-delete).
Unverified hyperscaler comparators using third-party prices: AWS t4g.medium 146 x 0.0384 = USD 5.61 plus EBS; Azure B2als_v2 146 x 0.0246 = USD 3.59 plus disk. Not ranked because the prices are not official.

### 4.4 Architecture (c): several customers per bare-metal host with microVMs
Formula: C_c = (P_host + setup/12) / N, where N = min(N_disk, N_ram).
- Reservations (my assumptions): 8 GB RAM for host. Active microVM = 4 GB RAM. Safety factor f=2 on average concurrency to absorb peaks. Idle customers are hibernated to disk (state on NVMe) and use no RAM. Disk usable = 90 percent of the RAID1 capacity, 40 GB per customer. SMT off for multi-tenancy (per Firecracker guidance), which halves threads; CPU is not the binding constraint in this model.
- N_ram = (RAM − 8) / (4 x f x d). N_disk = 0.9 x TB x 1000 / 40.

**Hetzner AX42, EUR 79/mo (min config 64 GB, 2x1.92 TB NVMe, RAID1 = 1.92 TB), setup EUR 49** [direct]
- N_disk = 0.9 x 1920 / 40 = 43.2 -> 43.
- N_ram: 5 percent: 56 / (4x2x0.05) = 140; 20 percent: 56 / (4x2x0.2) = 35; 60 percent: 56 / (4x2x0.6) = 11.7 -> 11.
- N = min: 43 / 35 / 11.
- C_c = 79 / N: **EUR 1.84** (5 percent), **EUR 2.26** (20 percent), **EUR 7.18** (60 percent). Add setup: 49/12 = 4.08 per month over N: +0.09 / +0.12 / +0.37.

**Hetzner AX41, EUR 59/mo (64 GB, 2x512 GB NVMe, RAID1 = 512 GB), setup 0** [direct]
- N_disk = 0.9 x 512 / 40 = 11.5 -> 11. N_ram same as above (140/35/11). N = 11 in all scenarios.
- C_c = 59 / 11 = **EUR 5.36** in all scenarios (disk-bound).

**Hetzner AX102, EUR 159/mo (128 GB, 2x1.92 TB min), setup 129** [direct]
- N_disk = 43; N_ram = 120 / (8 d): 300 / 75 / 25 -> N = 43 / 43 / 25.
- C_c = 159/43 = **EUR 3.70** (5 percent and 20 percent), 159/25 = **EUR 6.36** (60 percent). Larger NVMe options raise the price by an amount I could not map (page ambiguity), so not computed.

**Sensitivity**: if each customer needs 20 GB instead of 40 GB, N_disk doubles to 86 for AX42 and the 5-percent cost falls to 79/86 = EUR 0.92 (RAM then binds at 140/35/11 vs 86, so 5 percent: 86 -> 0.92; 20 percent: 35 -> 2.26; 60 percent: 11 -> 7.18). If a customer's Chromium needs 8 GB active, halve N_ram.

Not included in (c): hypervisor/microVM engineering and on-call, a second host for failure tolerance, off-host backups (Hetzner Storage Box or Object Storage price NOT FOUND), egress (unlimited on dedicated), IP addresses for per-customer isolation, monitoring.

### 4.5 Observations on cost (explicitly not a decision)
- Hosting compute per active customer is in the low single-digit EUR range for architectures (b) and (c) at 5-20 percent duty, roughly EUR 1-8 per month on the documented numbers above. Always-on (a) is EUR 4-35 depending on provider, and does not benefit from low duty.
- The documented cheapest always-on VPSs (OVH VPS-1, Aruba O2A4, IONOS promo, netcup, Strato) cost EUR 3.8-10 per month, which is comparable to or below (b) at 20 percent duty on a big-name cloud; the benefit of (b) shows mostly against expensive hourly clouds and at very low duty.
- Hetzner's 2026 price rises (CPX22 EUR 7.99 to 19.49) show that list prices can move by multiples within a year, and that CX/CAX capacity can be withdrawn. Any plan that relies on one cheap line should keep a price-change margin.
- Against the ~EUR 60/month reference plan: documented hosting for one customer is a small fraction at 5-20 percent duty; the likely larger cost drivers are LLM API usage, support and engineering, none of which this research covers.
- The cheapest architecture (c) concentrates risk on one host and requires engineering of microVM orchestration; (a)/(b) per-customer VMs use provider APIs but, on Hetzner Cloud, cannot nest microVMs.

---

## 5. Sources (all accessed 2026-10-28)

Hetzner
- https://www.hetzner.com/cloud/ ; https://www.hetzner.com/cloud/cost-optimized/ ; https://www.hetzner.com/cloud/regular-performance/ ; https://www.hetzner.com/cloud/volumes/
- https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/
- https://docs.hetzner.com/cloud/servers/faq/
- https://www.hetzner.com/legal/terms-and-conditions/
- https://www.hetzner.com/dedicated-rootserver/matrix-ax/ ; https://www.hetzner.com/sb/
- Third party on price history: https://agentdeals.dev/hetzner-pricing-2026 ; https://www.bitdoze.com/hetzner-cloud-cost-optimized-plans/ ; https://www.vincentschmalbach.com/hetzner-cheap-cloud-unavailable-price-increases/

OVHcloud: https://www.ovhcloud.com/it/vps/ ; https://www.ovhcloud.com/en/vps/ ; https://www.ovhcloud.com/en/bare-metal/prices/ ; https://blog.ovhcloud.com/en/posts/dedicated-servers-pricing-update-h2-2026/

Scaleway: https://www.scaleway.com/en/pricing/virtual-instances/ (403 on direct fetch; figures via search extract) ; https://www.scaleway.com/en/blog/a-transparent-update-on-scaleway-pricing/ (403) ; https://www.vpsbenchmarks.com/hosters/scaleway ; https://www.eucloudcost.com/providers/scaleway/

Others
- IONOS: https://www.ionos.it/server/vps ; https://www.ionos.com/servers/vps
- netcup: https://www.netcup.com/en/server/vps
- Strato: https://www.strato.de/server/linux-vserver/
- Hostinger: https://www.hostinger.com/vps-hosting
- Infomaniak: https://www.infomaniak.com/en/hosting/vps-lite (page text truncated; CHF numbers via search extract)
- Aruba: https://www.arubacloud.com/vps/ (via search extract) ; https://www.aruba.it/listino-cloud.aspx
- Contabo (third party only): https://www.eucloudcost.com/providers/contabo/ ; https://hostingsift.com/hosting/contabo
- Vultr (third party only): https://cloudpricecheck.com/vultr/vultr-compute-pricing ; https://betterstack.com/community/guides/web-servers/vultr-review/
- Akamai/Linode: https://www.akamai.com/cloud/pricing ; https://www.akamai.com/cloud/pricing/cpu
- DigitalOcean: https://www.digitalocean.com/pricing/droplets ; https://docs.digitalocean.com/products/droplets/details/pricing/
- UpCloud (third party): https://upcloud.com/pricing/ (403) ; https://www.vpsbenchmarks.com/hosters/upcloud/plans/4gb2cores
- Exoscale: https://www.exoscale.com/pricing/
- AWS: https://aws.amazon.com/ec2/pricing/on-demand/ ; https://aws.amazon.com/lightsail/pricing/ ; nested virt: https://www.infoq.com/news/2026/03/aws-ec2-nested-virtualization/ ; https://www.techzine.eu/news/infrastructure/138831/aws-expands-ec2-with-support-for-nested-virtualization/ ; t4g prices: https://sparecores.com/server/aws/t4g.medium
- Google Cloud (third party): https://gcloud-compute.com/e2-medium.html ; https://sparecores.com/server/gcp/e2-medium ; https://cloud.google.com/compute/vm-instance-pricing (not readable)
- Azure (third party): https://www.azurespeed.com/AzureVmPricing/Standard_B2s ; https://azurehub.org/tools/vm-pricing/
- Oracle: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm ; https://www.oracle.com/cloud/price-list/ (extract) ; https://www.infoq.com/news/2026/07/oracle-cloud-free-tier-limits/
- IBM (third party): https://pcr.cloud-mercato.com/providers/ibm/flavors/bx2-2x8/pricing
- Alibaba/Tencent: https://intl.cloud.tencent.com/document/product/1103/47794 ; https://getdeploying.com/alibaba-cloud
- Seeweb: https://www.seeweb.it/en/products/cloud-server-shared-cpu
- TIM/Register/voucher (search extracts): https://www.corrierecomunicazioni.it/digital-economy/cloud/voucher-cloud-cybersecurity-operatori-tlc/ ; https://omc.cloud/vs/register-it/it
- OpenMetal: https://openmetal.io/answers/enterprise-hardware/openmetal-medium-v4-bare-metal-server-specs/ ; https://www.whtop.com/plans/openmetal.io/147119 (third party)
- Equinix Metal: https://docs.equinix.com/goodbye-metal/
- Firecracker: https://github.com/firecracker-microvm/firecracker ; https://github.com/firecracker-microvm/firecracker/blob/main/docs/prod-host-setup.md
- Isolation comparisons (third party): https://northflank.com/blog/kata-containers-vs-firecracker-vs-gvisor ; https://bex.co/blog/2026/09/22/gvisor-vs-kata-vs-firecracker-agent-sandbox-isolation ; https://opencomputer.dev/guides/firecracker-vs-cloud-hypervisor-vs-kata/

---

## 6. NOT FOUND / needs owner decision

Not found (price or fact):
- Hetzner: auction server list and prices (JS page); primary IPv4 and volume prices; whether CX/CAX return to stock; official statement on scraping/automation.
- OVH: monthly (no-commitment) VPS price; Italy DC; EUR dedicated list prices for Kimsufi/Rise; Public Cloud hourly prices; nested KVM on VPS.
- Scaleway: direct official confirmation of prices and stopped-instance billing; Elastic Metal prices; Italy DC.
- Infomaniak: VAT status, Public Cloud hourly prices.
- Aruba: stopped billing, snapshots, nested KVM, current hourly Cloud Server prices.
- IONOS: post-promo price and minimum term; IONOS Cloud compute prices.
- Contabo, Vultr, UpCloud: official prices (pages blocked, 403).
- Exoscale: all EUR prices (placeholders).
- AWS: official EC2 prices for Milan/Frankfurt; C8i/M8i prices; Spot; stopped Lightsail billing.
- GCP and Azure: official prices; Spot in Milan/Frankfurt/Italy North.
- Oracle: Milan/Frankfurt availability and commercial permission to use Always Free.
- IBM, Alibaba, Tencent, Register, TIM (VAT), Fastweb, latitude.sh, OpenMetal list prices.
- DPA/GDPR links and AUP positions on browser automation/scraping for every provider except Hetzner (Terms read, scraping not addressed).
- Snapshot/restore time of a headed Chromium profile on any provider; no benchmark was done.
- GPU prices for any provider (not in scope for the basic tier).

Needs owner decision (facts that would change the answer):
1. Required RAM per active customer (4 GB vs 8 GB) and per-customer disk (20 vs 40+ GB).
2. Is Italy-only data residency required, or EU/EEA, or is Switzerland acceptable (Infomaniak, Exoscale)?
3. Are US-headquartered providers acceptable in EU regions given CLOUD Act exposure?
4. Are Chinese-headquartered providers excluded?
5. Willingness to operate microVM orchestration (Firecracker/Incus/Proxmox) versus using a provider API per customer VM.
6. Customer-visible latency of waking a hibernated assistant (affects stop-versus-delete choices).
7. Price-change tolerance (providers moved prices up several times in 2026).
8. Whether an EU/consumer-law review is needed for selling to private individuals (VAT 22 percent, GDPR role of the operator).

---

## 7. Limits of this research

- Web access was proxy-limited: many official pages (Scaleway, Contabo, Vultr, UpCloud, GCP, AWS price list) returned 403 or JS-only content. Several "official" numbers are from search-engine extracts of those pages and are tagged as such. Page text summarised by the fetch tool may contain errors, especially for long tables.
- The fetch tool returned US-geo pricing (USD) on several global sites; the EUR/EU price can differ. VAT status was often not stated.
- Prices are volatile: Hetzner and OVH changed prices in 2026, and Hetzner's own pages disagree with each other by EUR 0.50. Everything may be stale by the time of use.
- No tests were run: no benchmarks, no nested-KVM checks, no verification of hibernation or snapshot behaviour, no check that Chromium runs in any given sandbox.
- AUP/ToS: I read only Hetzner's terms. Most providers have fair-use or abuse policies that could affect automated browsing and account-bound logins; none were checked.
- Items tagged [bg] come from my background knowledge and were not verified in this session, in particular API availability, HQ details and some data-centre lists.
- The list of providers is broad but not exhaustive; some small EU hosts, GPU clouds, and managed microVM sandboxes (outside R1 scope) are not covered.
- The cost model uses assumptions (duty cycle, state size, concurrency factor, no overhead) that I chose; it excludes LLM costs, backups, support, IPv4 and engineering time.

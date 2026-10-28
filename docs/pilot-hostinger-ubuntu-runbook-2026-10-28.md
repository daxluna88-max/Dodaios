# Pilot runbook: Jarvis on a Hostinger KVM 2 (Ubuntu 24.04 + XFCE remote desktop)

Status: PREPARED, NOT RUN. Nothing here was executed or checked against Hostinger. Owner buys and operates; no
secret goes in this file or in the repo. This is a real-test box for ONE user (the owner), not the customer
cloud design: the broker, tenant auth and signed updates are still missing (ADR-0067/0068, review G1-G11).

## 0. Before paying (owner, 5 minutes)
- Confirm in the Hostinger checkout: plan KVM 2 or higher, OS template "Ubuntu 24.04 with Remote Desktop (XFCE)",
  region, billing term. Record exact price and renewal terms (unconfirmed until the checkout shows them).
- Nested virtualization is NOT needed: the VPS itself is the isolated environment.
- Use a fresh password manager entry; enable 2FA on the Hostinger account.

## 1. First hour (owner at the console, Claude gives commands)
1. Change the root/initial password; create a non-root sudo user; add an SSH key; disable password SSH.
2. `ufw default deny incoming`; allow 22 only from the owner's IP (or leave SSH on a non-default port).
   The remote desktop port (RDP 3389 / VNC) must NOT be open to the world: reach it through an SSH tunnel
   (`ssh -L 3390:localhost:3389 user@host`, then connect to localhost:3390) or an allow-listed IP.
3. `apt update && apt upgrade`; reboot; take a Hostinger snapshot (convenience, not a backup).
4. Install Dodaios per `docs/headless-vps-deployment.md` section 1, but WITH the desktop (omit `--headless`
   only if screen control is wanted; XFCE gives it a screen). Record the exact commit installed (checklist A02).
5. Keys: put only the keys needed for the first test in the user environment file (mode 600), with a low
   spend cap set at the provider. No key in logs, screenshots or the repo.

## 2. Tests worth doing first (map to issue #16 checklist; all start NOT RUN)
- A01/A02: inventory (`lscpu`, `free -h`, `df -h`, `uname -a`) and installed SHAs.
- B01: app starts from the real command and the UI answers from the owner's browser through the tunnel.
- E01-E03: voice from the owner's browser microphone (server has none): short/long phrase, first-word latency.
- F01/F02: approvals, STOP, memory proposal -> approval -> restart persistence.
- C04: offline claim needs OS-level network observation; do not claim offline from the VPS test.

## 3. Rules for the box
- Single user, own data only. No customer data, no payments, no public 443 until the ingress model is chosen
  (ADR-0067 erratum).
- FAIL stops the dependent group; write the defect, do not retry in a loop.
- Record per test: ID, SHA, result, date, evidence without secrets, actual cost.

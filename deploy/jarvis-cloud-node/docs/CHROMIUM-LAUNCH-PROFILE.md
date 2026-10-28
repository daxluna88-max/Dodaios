# Chromium / Playwright launch profile (DEFINITION - NOT RUN)

Nothing here was executed: no browser launched, no AppArmor profile loaded, no sysctl read.
Facts about Ubuntu 24.04 come from public documentation and bug reports (URLs at the end), not from a test.

## 1. Decision: the Chromium sandbox stays ON

ADR-0065 D9 said `--no-sandbox` is acceptable "inside a microVM". That reasoning does not carry over to
**one VPS per customer**: the VM boundary protects *other customers*, but a renderer exploit from a hostile
page would still land as user `jarvis` with the customer's whole home, the logged-in browser profile (cookies,
sessions), the workspace and the broker token. The sandbox is what separates the page from those. This
definition therefore **revises D9**: no flag in any unit, launcher or config sets `--no-sandbox`;
`bin/chromium-launch` refuses it; the managed policy and systemd units do not need it.

## 2. The Ubuntu 24.04 problem

Ubuntu 24.04 ships `kernel.apparmor_restrict_unprivileged_userns=1`. Any process may create a user namespace, but
inside it the `unprivileged_userns` profile denies capabilities (e.g. `CAP_SYS_ADMIN`), so Chromium's namespace
sandbox fails to set up ("No usable sandbox" / "The SUID sandbox helper binary was found, but is not configured
correctly"). Playwright's Chromium does not ship a setuid `chrome-sandbox`.

## 3. Options, with security trade-offs

| # | Option | Effect | Security trade-off | Verdict |
|---|---|---|---|---|
| A | **Per-binary AppArmor profile with `userns,`** (`apparmor/jarvis-chromium`, `flags=(unconfined)`), attached by the path of the root-owned Playwright binary in the immutable release dir | Only that executable may create a capable userns; sysctl stays 1 for everything else | Relaxes the restriction for one binary; profile is unconfining otherwise; anything that can execute that path gets userns caps (the agent can run it, but it is the browser it can run anyway; a malicious *copy* elsewhere is not covered). Path glob must track the Playwright revision. Under `NoNewPrivileges=yes` the AppArmor exec transition must be verified | **Chosen** |
| B | `sysctl kernel.apparmor_restrict_unprivileged_userns=0` | Everything works, no profile | Re-opens unprivileged-userns capabilities for **every** program incl. the agent's shell tools: larger kernel attack surface (userns is a frequent enabler of kernel LPEs); also the restriction has had public bypasses, so it is a hardening layer, not a wall | Fallback only, with owner sign-off, recorded as accepted risk |
| C | Setuid `chrome-sandbox` helper (root, 4755) | Legacy path | Adds a setuid-root binary reachable by the agent; Chromium has been moving away from it; not shipped by Playwright | Rejected |
| D | `--no-sandbox` | Works anywhere | Removes the page-to-profile boundary (section 1) | Rejected as default. If ever used for a diagnostic, never in a unit file, never persisted |
| E | Snap/apt `chromium-browser` | Packaged AppArmor handling | It is a snap shim on Ubuntu, bypasses the pinned Playwright build, and `session.py::system_chrome()` would prefer it by PATH | Rejected (apt pin in cloud-init) |

Test plan (NOT RUN): on a throwaway 24.04 host, (1) launch the pinned Chromium as an unprivileged user without
the profile and confirm the failure; (2) load the profile and confirm the sandbox starts (`chrome://sandbox` is
blocked by policy, use the launch log or `--enable-logging=stderr`); (3) confirm an arbitrary other binary still
cannot get caps in a userns; (4) repeat under the systemd unit with `NoNewPrivileges=yes`; if the transition is
refused, decide between dropping NNP on the runtime unit and a `Cx` child profile.

## 4. Launch profile

| Item | Value |
|---|---|
| Binary | Playwright build pinned by `society/browser/install.py` (`PLAYWRIGHT_VERSION`), under `/opt/jarvis/current/browsers` (read-only, root-owned). `PLAYWRIGHT_BROWSERS_PATH` points there; the code's runtime venv/browser installer (`society/browser/install.py`) must not run (gap G5) |
| System browsers | None installed; `system_chrome()` must find nothing on PATH |
| Profile (persistent login) | `/data/jarvis-consumer/society/<agent>/browser-profile` (`session.py::profile_dir`), mode 0700, on the customer volume; one browser at a time (Chromium `SingletonLock`) |
| Cache | `/data/cache/chromium-<agent>` (excluded from backup; sized cap) |
| Downloads | `DownloadDirectory=/data/home/Downloads` via policy; `DownloadRestrictions=1`; the unit's `ReadWritePaths` limit what the browser can write; agent moves files out explicitly |
| CDP | **No `--remote-debugging-port` in this definition.** Preferred: Playwright pipe transport (`--remote-debugging-pipe`, no socket). KNOWN GAP G6: browser-use 0.13.x (pinned) drives Chromium over CDP and, as far as read, picks a loopback port; loopback is not reachable from the network (nftables input drop, proxy has no `/json` route) but IS reachable by any process of user `jarvis`. Same trust domain as the agent, so accepted for the pilot; verify the port binding and prefer pipe transport |
| Extensions | Policy `ExtensionInstallBlocklist=["*"]`, `BlockExternalExtensions`; `BROWSER_USE_DISABLE_EXTENSIONS=1` already set by `install.py`; no `--load-extension` (launcher refuses) |
| `file://`, `chrome://` | `URLBlocklist` blocks `file://*`, `chrome://*`, `chrome-extension://*`, `view-source:`, `javascript:`, `ftp:`. Trade-off: the agent cannot open a downloaded local HTML/PDF in the browser; use a local viewer tool instead. TODO(verify) the policy does not break Playwright internals (`about:blank` is allow-listed) |
| DevTools | Disabled by policy for the human; automation protocol unaffected (TODO verify) |
| Passwords/sync | Chromium password manager and sync off; `--password-store=basic` (no libsecret, no Secret Service on the bus) |
| Network | Egress exclusively via nftables tier policy; `WebRtcIPHandling=disable_non_proxied_udp`; QUIC dropped at the firewall |
| Resource limits | `jarvis-agent.slice` (6G/380%/2048 tasks); `jarvis-browser@` 3.5G/250%; `/dev/shm` from `PrivateDevices`; tab/window bound: set by the runtime (gap G8: browser-use has no hard tab cap known to this review) |
| GPU | none: software rendering, `--disable-gpu` is acceptable (it is not `--disable-gpu-sandbox`) |
| Display | `DISPLAY=:10` (Xvfb), X11 only, no Wayland |

## 5. Sources (not re-verified in a run)
- AppArmor issue on `unprivileged_userns` vs Chromium: https://gitlab.com/apparmor/apparmor/-/issues/394
- oss-security discussion (restriction bypasses, `--no-sandbox` not recommended): https://seclists.org/oss-sec/2024/q2/147
- Chromium Linux sandboxing docs: https://chromium.googlesource.com/chromium/src/+/main/docs/linux/suid_sandbox.md
- Ubuntu's own explanation (page returned HTTP 500 when fetched): https://ubuntu.com/blog/ubuntu-23-10-restricted-unprivileged-user-namespaces

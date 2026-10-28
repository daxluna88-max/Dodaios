# D1-D20 dependency map after this definition (NOT RUN)

Baseline: one OVHcloud-class Linux VPS (4 vCore / 8 GB / 75 GB NVMe) per customer, always-on pilot, no purchase.
"Defined" = a definition file exists here; it was never executed. Differences from ADR-0065 are marked REVISED.

| # | Dependency | Status after this definition | Where | Code gap still to implement |
|---|---|---|---|---|
| D1 | OS keyring / secrets | Defined: file backend stays, but holds no provider key; broker token via `LoadCredential` | docs/VOLUME-AND-UPDATES.md s2, jarvis-runtime.service | G1: `dodaios/core/config.py` (`get_secret`, `_install_file_cred_backend`, `_FileCredStore`) has no "broker token" mode; ADR says `.secrets/`, code writes `credentials.json` |
| D2 | Data root / HOME | Defined: `/data/jarvis-consumer`, `HOME=/data/home` | node.env.sample, jarvis-data-init.sh | Verify `product_profile.check_product_root(canonical=True)` accepts this path on a headless host; audit ~105 `Path.home()` uses |
| D3 | Control Key / internet auth | Defined as hook only (`forward_auth`) | proxy/Caddyfile.sample | G3: session/account service not written; injecting the node control key; `ui/web/control_auth.py`, `surface_security.py` Host/Origin for the public host |
| D4 | Loopback / bind | Defined: runtime 127.0.0.1:49821 (ADR-0054 offset), Caddy on 443 | units, nft | G11 (new, security): behind a local proxy every peer is loopback; `surface_security.is_loopback_request` and `browser_voice/route.py::_socket_peer_is_loopback` (line ~91, used at ~268) would treat remote users as local unless relay headers are honoured. Needs review before exposing |
| D5 | Desktop shell | Not installed (no `[desktop]` extra) | cloud-init | none |
| D6 | Audio | Browser mic via `/ws/audio`; Caddy forwards WebSocket; `libportaudio2` for imports | Caddyfile | WebRTC later; Origin check vs `browser_voice` auth (`credentials_valid`) to test |
| D7 | Input / hotkeys | XTest on Xvfb :10 | jarvis-xvfb.service | none new |
| D8 | Computer use / screen | Defined: persistent Xvfb :10 + openbox + session bus + x11vnc/noVNC live view | jarvis-xvfb/wm/dbus/vnc/novnc | G4: `dodaios/agent_screen/providers/xvfb.py::XvfbProvider.start` always spawns its own Xvfb (:90-129) and a runner; needs an "attach to existing display :10" mode. `cu/direct.py::readiness` and AT-SPI via the bus untested |
| D9 | Browser | REVISED: sandbox kept (was "--no-sandbox acceptable in a VM"); AppArmor `userns` profile; policy file; no CDP port | docs/CHROMIUM-LAUNCH-PROFILE.md, apparmor/, chromium-policy/ | G5: `society/browser/install.py` builds a venv + downloads browsers at runtime into DATA_DIR (pip/uv at runtime) -> must read from the release bundle; G6: CDP/loopback port of browser-use, one-browser lock vs `jarvis-browser@`, verify how browser-use 0.13.10 sets the sandbox flag in `society/browser/runner.py` (`Browser(...)` kwargs) ; `session.py::_request_base` `attach` branch and `manual_chrome.py` to disable; `system_chrome()` must return None; G8: tab cap |
| D10 | Shell / MCP | Allowed as `jarvis`, no sudo; egress via nft; slice limits | units, nft | none new (app guard is not a sandbox) |
| D11 | Vendor CLI logins | Not provided (open, terms) | - | unchanged |
| D12 | Python egress guard | Defence in depth only; real control is nftables by uid | nftables/jarvis-node.nft | none |
| D13 | Launch lock | Kept | - | none |
| D14 | Self-update | Defined: immutable A/B releases, signed bundle, supervisor-only activation | bin/jarvis-release-activate.sh | G2: add a cloud-node switch disabling `ui/relauncher.py`, `app_update`, `dodaios update`, `autostart/*` |
| D15 | Marketplace / self_mod | Off by decision pending (open) | - | G2: config/flag to hard-disable `marketplace/*`, `core/self_mod/*` |
| D16 | SQLite / backup | Layout defined; off-VM verified backup is control-plane work | VOLUME-AND-UPDATES.md | `core/product_backup.py` coverage of home/, wiki, skills, browser profile (ADR-0064 gap) |
| D17 | Logs with content | Defined: content on volume, Caddy/nft metadata only, journald bounded | s3 | G9: operator log field allow-list (`core/redact.py` is redaction, not allow-list) |
| D18 | Metrics / phone pairing | Metrics 11090 stays loopback (no proxy route); pairing replaced by sessions | Caddyfile | `phone/*` pairing replacement not designed |
| D19 | Desktop mirror / packaging | Skipped | - | none |
| D20 | Process singletons | Fine: one runtime per VPS | - | none |

Additional gaps: G7 real AppArmor confinement for the browser (profile is unconfined+userns only);
G10 `jarvis-novnc` account and a per-boot VNC password generator are referenced but not written;
inference broker service and its egress allow-list entry are control-plane work, not defined.

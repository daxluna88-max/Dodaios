# ADR-0054: Work and Consumer are two independent products

Status: accepted (launch isolation only; installers and signing not done)

## Context

The Work product (local inference only) and the Consumer product share source code but must
not share state: a Consumer key readable from Work, or one product's upgrade rewriting the
other's data, would defeat the Work policy (ADR-0049).

## Decision

`python -m dodaios --product work|consumer` (`dodaios.core.product_profile`) gives each product:

- its own data directory, `<shared data dir>-<product>` (a sibling, never nested), which holds
  its config (`JARVIS_CONFIG`), credential file and stores (`JARVIS_DATA_DIR`);
- its own credential-store service, `dodaios-work` / `dodaios-consumer`, with **no** fallback to
  the shared `dodaios` service (`LEGACY_KEYRING_SERVICES` is empty), so keys are not readable
  across products and nothing is copied from the shared vault;
- an atomic ownership marker `.product`: a directory claimed by the other product, or a
  corrupt/unknown marker, is refused, never adopted. Re-launching and upgrading the same
  product reuse the directory;
- a per-product launch lock: a second launch of the same product is refused, the other product
  runs alongside.

- its own ports: every configured port (`ui.admin_api_port` 47821, `mcp_server.http_port` 47822,
  `telemetry.metrics_port` 9090) sits above its configured base by `PRODUCT_PORT_OFFSETS`
  (`dodaios.core.instance`): Work +1000, Consumer +2000, so 48821 / 49821 for the admin port. The
  offset is read from `JARVIS_PRODUCT_ACTIVE`, so children and the restart chain bind the same
  ports. It stacks with the dev instance's +100 and no combination collides (unit-tested). The
  unscoped app keeps 47821 and the desktop focus probe no longer falls back to it from a product.

The environment (`JARVIS_PRODUCT`, data dir, config, credential service) is inherited by child
processes. An unknown product name is an error, not a default.

## Consequences / limits

- Not verified: the two products running side by side on a real machine (checklist B04 of
  Jarvis-di-dax issue 16, NOT RUN), other fixed ports not listed above (voice worker, avatar
  server), OS identity (window title, icon, Windows AppUserModelID,
  autostart entries), installers, signing, upgrades of a real installed build, macOS/Windows.
- `JARVIS_PRODUCT` set by hand without `--product` still selects the policy but shares the
  vault and directory; use the flag.
- Nothing migrates existing shared data or keys into a product; that is a separate, explicit
  step.

<!-- BEGIN block 5: mirror and uninstall -->
## Block 5: mirror and uninstall

Written statically: **nothing was run** (no pytest, no test, no build, no uninstall, no app). Only
`py_compile`, `bash -n` and `ruff check`.

### Ownership

The install folder (`cfg.PROJECT_ROOT`, its venv, the OS app registration, the login autostart
entry, the terminal commands, the earlier-name links) is **shared** by both products. Each product
owns only its data directory `<shared>-<product>` (marker `.product`), its credential service
`dodaios-<product>`, and its Desktop output mirror.

### What changed

- **Desktop mirror** (`dodaios/core/paths.py`). A product launch uses its own root,
  `~/Desktop/Dodaios-Output-<product>`; the unscoped app keeps `Dodaios-Output`. On Windows
  `ensure_session_output_dir` no longer uses whatever is at the mirror path: it is used only if it
  is a directory link that resolves to this product's own canonical folder (`mirror_is_safe`). A
  link elsewhere (for instance the other product's output), a real folder, or a mirror root that
  is itself a link is left untouched, never followed or deleted, and the canonical path is
  returned. The mirror is also returned only if `mklink` really produced a safe link.
- **Per-product uninstall** (`dodaios/setup/uninstall.py`). Run as
  `python -m dodaios --product <work|consumer> --uninstall`: removes only that product's data
  directory (it must be a real directory named `*-<product>`, not a link, carrying that product's
  marker, outside the install folder) and the keys of its own credential service. It never removes
  the install folder, registration, autostart, terminal commands or processes found by install
  folder. `--keep-folder` keeps the data directory, `--keep-keys` the keys. Any doubt (no marker,
  foreign marker, wrong credential service, link, overlap, contradicting `--product`) exits 3,
  nothing changed.
- **Full uninstall guard.** Unscoped, `run_uninstall` exits 3 while a Work/Consumer data directory
  exists (current or earlier shared name) or the scan cannot tell, also on `--dry-run`. Remove
  each product first, then the shared install.
- **Bootstraps** (`install/uninstall.sh`, `install/uninstall.ps1`). Exit code 3 from the Python
  step is reported as a refusal, with no "delete it by hand" advice. With `--product` they never
  delete the install folder. Without a working venv they refuse (exit 3) if `--product` was given
  or if a product data directory exists next to the shared one.

### NOT done / NOT verified

- Nothing was executed. The new tests (`tests/unit/core/test_desktop_mirror_products.py`,
  `tests/unit/setup/test_uninstall_products.py`, `tests/unit/install/test_uninstall_bootstrap_products.py`)
  are written, not run.
- Real Windows junction behaviour, Windows locks on the running product's data directory during
  its own removal (the product's launch lock file is open while it is deleted), macOS Keychain
  and Windows Credential Manager removal under `dodaios-<product>`: not verified.
- The shell/PowerShell product data paths in the no-venv fallback assume the default locations
  (`~/.dodaios-data-<product>`, `%LOCALAPPDATA%\Dodaios-<product>`); a custom data location is not
  seen there.
- Identity is still shared and NOT separated: macOS `Dodaios.app` and bundle id
  `com.personal-jarvis.desktop`, the autostart label/entry, the Windows Start menu shortcut,
  Installed Apps key, AppUserModelID, the Linux `.desktop` entry and the terminal commands. The
  full uninstall removes them only when no product data is left; a per-product install with its
  own folder, shortcut, bundle id and autostart entry is not implemented.
- Shared-install protection is by refusal only: a full uninstall does not offer a "remove
  everything including products" mode; no product-selecting flag exists in the bootstraps
  beyond passing `--product` through.
- Old Desktop output under `Dodaios-Output` made before this change is not moved or re-linked.
<!-- END block 5: mirror and uninstall -->

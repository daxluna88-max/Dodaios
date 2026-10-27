# ADR-0064: Desktop packaging readiness (block 16)

Status: partial. Static preparation only.

**Build e verifica nell'app restano rinviate all'autorizzazione.** Nothing in this block was built,
installed, uninstalled, signed, notarized, tested or run: no PyInstaller, no npm/vite, no installer
builder, no pytest, no CI, no app, no download, no push. Only reading, editing, `py_compile`,
`bash -n` and `ruff check` were used. PowerShell and Inno Setup text was read, not executed. The two
new test files are written, not run.

## 1. Bundle vs code

State: **partial**. Python/data side: done. Frontend bundle: **BLOCKING, open**.

Audit of what blocks 5-13 added (since `main` 01cdb725), against `dodaios.spec`, `pyproject.toml`:

| Added | Covered by | Result |
|---|---|---|
| New Python modules (`safety/effects|decision|preflight`, `brain/provenance|personality|mood|style*|multi_engine_chat|consumer_orchestrator`, `core/child_identity|egress_guard|product_profile`, `control/stop|strip_state|turn_record`, `speech/stop_gate|stop_parts`, `memory/control/*`, `setup/first_run`, `missions/isolation/worker_registry`, `realtime/llm_bridge`, five new `ui/web/*_routes`) | PyInstaller import analysis: each one is imported by another module (read with grep, function-level imports included) | found; now also **named** in `BLOCK_MODULES` in the spec so a refactor of one importer cannot drop them |
| `memory/control` package | `collect_submodules("dodaios.memory.control")` added to the spec | done |
| `voice_engine/bridge_llm.py` | spec copies every file of `dodaios/voice_engine` as plain files | already covered |
| Backend locales `dodaios/i18n/locales/{de,en,es,pt}.json` (modified, no new file) | spec `datas` + `pyproject` `i18n/locales/*.json` | already covered |
| **No `it.json` exists** (backend or frontend). Italian is not a shipped UI/system-phrase language; the frontend has `zh` beside de/en/es/pt | n/a | stated, not invented |
| Frontend locale namespaces `first_run_hardening`, `memory_control`, `personality_limits` | compiled into `dist/` by Vite; not runtime data | covered only by a rebuilt `dist` (below) |
| SQL migrations | spec package-data sweep + `pyproject` `**/*.sql` | no new `.sql` in these blocks |
| ADRs / docs | not shipped (spec ships only `docs/product`, `docs/legal/TERMS.md`, `docs/licensing.md`) | by design |
| `dodaios/core/product_backup.py` (this block) | named in `BLOCK_MODULES` | done |

Unreachable code noticed: `dodaios/brain/engine_routing.py` is imported only by its own test, so the
freeze never contains it. Not a bundle gap; a dead module (left alone, outside this block's scope).

**Committed `dist/` is stale: BLOCKING for any release.** `dodaios/ui/web/dist` was last committed in
`bdd50306` (2026-10-27 08:38). Twelve later commits touch `dodaios/ui/web/frontend/src` (ControlStrip
ADR-0058, first-run hardening, memory control, personality limits, block 10-13 UI), the latest being
the merge `20536ff6`. So the shipped UI predates the control strip, memory control and every block 9-13
UI change. Static expectations of the gates:

- `scripts/ci/check_dist_consistency.py` (gate `dist-consistency`) only checks that the chunk graph
  from `dist/index.html` is tracked in git and has no orphans. It does **not** compare dist with the
  source, so it would pass on this stale bundle. It proves nothing about freshness.
- `scripts/ci/check_release_completeness.py` check 4 ("dist freshness") compares the last commit
  time on the frontend sources with the last commit time on `dist/`. By the git history above it
  **fails** today (source newer than dist). That gate is the right stop and must not be bypassed.
- Resolution is a rebuild of `dist` (never hand edits), listed in the checklist. The branch map already
  records that dist conflicts are resolved by rebuilding.

## 2. Per-product install identity

State: **design written; guards partial; per-product install not implemented.**

Today (ADR-0054 blocks 5 and 8): Work and Consumer share one install folder, one app bundle id /
`AppId`, one autostart entry, one Start menu / `.desktop` entry, one AppUserModelID, one set of
terminal commands, one update feed. They separate only data dir, credential service, ports, launch lock
and Desktop output mirror.

Design for side-by-side separate installs (**not implemented**):

| Identity | Shared today | Per-product design |
|---|---|---|
| App name | `Dodaios` | `Dodaios Work` / `Dodaios Consumer` |
| Install folder | `%LOCALAPPDATA%\Programs\Dodaios`, `~/.dodaios`, `Dodaios.app` | `...\Dodaios Work`, `~/.dodaios-work`, `Dodaios Work.app` |
| macOS bundle id | `ai.personaljarvis.desktop` (spec) / `com.personal-jarvis.desktop` (managed) | `ai.dodaios.work`, `ai.dodaios.consumer` (privacy grants then separate per product) |
| Inno `AppId` | one fixed GUID | two GUIDs; separate "Installed apps" entries and uninstallers |
| Autostart | one task/entry/label | `Dodaios Work` / `Dodaios Consumer`, each launching with `--product` |
| Shortcut / `.desktop` | one | one per product, `StartupWMClass` and AUMID per product (`InstanceIdentity.windows_aumid` and `linux_*` are the extension point) |
| Terminal commands | `dodaios`, `jarvis` | `dodaios-work`, `dodaios-consumer` (the bare name stays only for the unscoped app) |
| Update channel / feed | one `releases/latest` asset set | per-product asset names and checksum list (`Dodaios-Work-Setup-x64.exe`, ...) so Work never offers a Consumer installer |
| Uninstall | one, refuses while product data exists | scoped to its own folder + registration + autostart; product data via `--product` as in block 5 |
| Signing | one SignPath configuration pinning the product name | one artifact configuration per product name |

Implemented guards (static, **not run**):

- `install/install.sh`, `install/install.ps1`, `install/installer.py` refuse `--product` (exit 3)
  before any step: a flag that would suggest a separate install that does not exist. Without the guard
  the flag died inside argparse only after Stage 1 had already cloned and built a venv.
  The installer deliberately does **not** refuse on an inherited `JARVIS_PRODUCT_ACTIVE`:
  `relauncher._installer_command` runs it inside a product process during every managed update.
- The updater keeps the product through the environment (managed path: `fresh_user_env` copies
  `os.environ`; relauncher validates it with `validate_entry`). That does not survive every frozen
  relaunch: macOS `open` (LaunchServices) and the Inno `[Run]` entry do not carry the old
  environment, so the update would have restarted the **unscoped** app on the shared data directory.
  Now `product_profile.relaunch_args()` yields `--product NAME` and it is passed as `open ... --args
  --product NAME` (macOS), as an argument to the new AppImage (Linux) and as `/PRODUCT=NAME` to Setup,
  read by `ProductParam` in `packaging/windows/Dodaios.iss` (Windows, value whitelisted to
  `work|consumer`). A garbled product aborts the handover (`InstallerUpdateError`).
- `product_profile.running_products()`: probes the launch lock of each product (excluding the
  caller's own). Used by `POST /api/update/apply` (409 `other_product_running`, not overridable by
  `force`) and by `dodaios update` (exit 3). Unknown counts as running (fail closed). A missing lock file
  means "never ran".

Residual (not implemented): everything in the design table; a running *other* product is protected by
this refusal, but the shared files remain shared; a product launched later from a half-replaced install
is not guarded; the lock probe is check-then-act.

## 3. Update, rollback, restore

State: **partial**.

- Product identity across update: verified by reading. Managed path: environment + `validate_entry`
  in `__main__`, `relauncher.main`, `app_update.main`; the relaunch carries no `--product` and re-uses
  the same directory. Frozen path: fixed above (argument per OS). Windows via Inno still depends on
  Setup being spawned detached; the `/PRODUCT=` value reaches `[Run]` only through the Setup command line.
- Rollback exists for the **managed** install: `finalize_pending_update` resets git to the previous
  revision, re-runs the installer, checks the UI bundle, honours the contract's `rollback_hook`. For the
  **frozen** install there is no rollback: the macOS swap restores the old bundle only if the second
  rename fails; Windows Inno replaces in place; Linux replaces the AppImage atomically. A bad frozen
  release has no automatic return (open; a kept previous installer/bundle is the design).
- Data backup before update: `relauncher.snapshot_user_state` saved only install-folder files; it never
  saw `<root>-<product>`. New `dodaios/core/product_backup.py`:
  - `snapshot_product_state()` (called by `finalize_pending_update`, after the old snapshot): copies the
    top-level regular files of the **active** product's validated directory (config, credential
    fallback file, SQLite stores; each ≤ 64 MB, total ≤ 512 MB, never links, marker or locks) to
    `<data dir>/.pre-update-backup/<stamp>/` with a SHA-256 manifest; keeps 3; never raises.
  - Restore: `python -m dodaios.core.product_backup list|restore --product work|consumer [--stamp S]`.
    Refuses unless the directory is the canonical one, the marker names the product, the manifest names
    the product, every file matches its hash, and the product's launch lock can be taken (product closed).
    Replaced files are kept as `*.before-restore`. It writes only inside that product's directory; the
    other product and the shared directory are never opened.
  - Limits: sub-directories (wiki, skills, logs, models) are **not** backed up; the backup lives inside the
    data directory, so a destroyed directory takes it along and `--product ... --uninstall` removes it;
    unscoped (shared) data is not covered; the frozen path calls no snapshot (no `finalize_pending_update`
    there) so frozen updates have **no** data backup yet (open).

## 4. Uninstall

State: **done in code (block 5), unchanged and not re-verified by running**.

`python -m dodaios --product <work|consumer> --uninstall` removes only that product's directory
(real directory, `*-<product>`, own marker, outside the install) and its `dodaios-<product>` keys;
`--keep-folder`, `--keep-keys`; any doubt exits 3. It never removes the install folder, registration,
autostart or commands. Unscoped full uninstall exits 3 while any product data exists or cannot be
ruled out; `install/uninstall.{sh,ps1}` pass `--product` through and never delete the folder with it.
Runtime sharing rule: the install is the runtime of both products, so it can be removed only after both
products' data are gone. Not implemented: a "remove everything including products" mode, and the Windows
Inno uninstaller is unaware of products (it removes the program folder only). Windows locks, Keychain /
Credential Manager removal: never exercised.

## 5. Files

`dodaios.spec`, `dodaios/core/product_profile.py`, `dodaios/core/product_backup.py` (new),
`dodaios/core/installer_update.py`, `dodaios/ui/relauncher.py`, `dodaios/ui/web/update_routes.py`,
`dodaios/cli/app_update.py`, `install/install.sh`, `install/install.ps1`, `install/installer.py`,
`packaging/windows/Dodaios.iss`, `tests/unit/packaging/test_bundle_manifest_consistency.py` (new),
`tests/unit/packaging/test_product_install_identity_guards.py` (new), this ADR.

## 6. Release checklist (Dax, with authorization; NONE of this was run)

1. Rebuild `dodaios/ui/web/dist` from the current frontend source (npm install, build); commit it whole;
   `check_dist_consistency.py` and `check_release_completeness.py` (dist freshness) must pass.
2. Frontend type-check and frontend tests; full Python test suite including the tests written and never
   run in blocks 5-16; ruff.
3. Confirm the tag equals `dodaios.__version__`; `scripts/ci` release gates.
4. Build per OS: Windows (`packaging/windows/build.ps1`), macOS arm64 + x64 (`build.sh`), Linux AppImage;
   run `check_frozen_macos_app.py` and the frozen smoke.
5. Signing: SignPath/Azure (Windows), Developer ID + notarization (macOS); keep the pinned product name.
6. Clean VMs per OS: install, first run, update from the previous release (check the product survives the
   relaunch on macOS `open --args`, Windows `/PRODUCT=`, Linux AppImage), restore from
   `.pre-update-backup`, uninstall.
7. Side by side on one machine: Work and Consumer running together; update refused while the other runs;
   per-product uninstall leaves the other intact; full uninstall refused while product data exists.
8. Decide before release: per-product identities (section 2) or an explicit statement that one shared
   install ships; frozen rollback and frozen data backup.

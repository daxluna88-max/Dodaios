# ADR-0064: Desktop packaging readiness (block 16)

Status: partial. Static preparation only.

**Build e verifica nell'app restano rinviate all'autorizzazione.** Nothing in this block was built,
installed, uninstalled, signed, notarized, tested or run: no PyInstaller, no npm/vite, no installer
builder, no pytest, no CI, no app, no download, no push. Only reading, editing, `py_compile`,
`bash -n` and `ruff check` were used. PowerShell and Inno Setup text was read, not executed. The new
test files are written, not run.

## 1. Bundle vs code

State: **partial**. Python/data side: done. Frontend bundle: **BLOCKING, open**.

Audit of what blocks 5-13 added (since `main` 01cdb725), against `dodaios.spec`, `pyproject.toml`:

| Added | Covered by | Result |
|---|---|---|
| New Python modules (`safety/effects|decision|preflight`, `brain/provenance|personality|mood|style*|multi_engine_chat|consumer_orchestrator`, `core/child_identity|egress_guard|product_profile`, `control/stop|strip_state|turn_record`, `speech/stop_gate|stop_parts`, `memory/control/*`, `setup/first_run`, `missions/isolation/worker_registry`, `realtime/llm_bridge`, five new `ui/web/*_routes`) | PyInstaller import analysis: each one is imported by another module (read with grep, function-level imports included) | found; now also **named** in `BLOCK_MODULES` in the spec so a refactor of one importer cannot drop them |
| `memory/control` package | `collect_submodules("dodaios.memory.control")` added to the spec | done |
| `voice_engine/bridge_llm.py` | spec copies every file of `dodaios/voice_engine` as plain files | already covered |
| Backend locales `dodaios/i18n/locales/{de,en,es,pt}.json` (modified, no new file) | spec `datas` + `pyproject` `i18n/locales/*.json` | already covered |
| **Backend** has no `it.json` (`dodaios/i18n/locales/` ships de/en/es/pt). **Frontend** `it.json` DOES exist at this SHA (corrected in block 17; the earlier statement was stale): the base `dodaios/ui/web/frontend/src/i18n/locales/it.json` and the namespace files `first_run_hardening/it.json`, `memory_control/it.json`, `onboarding/it.json`, `personality_limits/it.json` (4 namespace files by `git ls-files`; no `control_strip/it.json` is tracked at this SHA). The frontend also has `zh` beside de/en/es/pt | frontend: compiled into `dist/` by Vite (as below); backend: n/a | frontend Italian is shipped through a rebuilt `dist` only; backend system phrases stay without Italian |
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
  saw `<root>-<product>`. `dodaios/core/product_backup.py` (reworked in block 17, see section 7):
  - `snapshot_product_state_checked()` (called by `finalize_pending_update`): SQLite stores are copied with
    the online backup API (coherent), other top-level regular files with `copy2`; manifest of coverage and
    every omission with its reason; keeps 3. A failed snapshot is a refusal of the managed update, not a
    shrug (section 7).
  - Restore: `python -m dodaios.core.product_backup list|restore|recover --product work|consumer [--stamp S]`.
    Refuses unless the directory is the canonical one, the marker names the product, the manifest names
    the product, every file matches its hash, and the product's launch lock can be taken (product closed).
    Staged, journalled, rolled back on error (section 7). It writes only inside that product's directory;
    the other product and the shared directory are never opened.
  - Limits: sub-directories (wiki, skills, logs, models) are **not** backed up, so this is never a full data
    rollback; the backup lives inside the data directory, so a destroyed directory takes it along and
    `--product ... --uninstall` removes it; unscoped (shared) data is not covered; the frozen path calls no
    snapshot (no `finalize_pending_update` there) so frozen updates have **no** data backup yet (open,
    declared).

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

## 7. Block 17: GPT round 4 (H5, H6)

Nothing below was run: no pytest, no build, no installer, no restore or backup against real data, no
download, no push. Only reading, editing, `py_compile` and `ruff check`. The new tests are written, not run.

### H5 (HIGH): backup did not ensure a coherent SQLite snapshot

State: **fixed in code, not run.**

- Files: `dodaios/core/product_backup.py`, `dodaios/ui/relauncher.py` (`finalize_pending_update`,
  `_write_update_result`), `dodaios/cli/app_update.py` (`--allow-unbacked-update`, refusal message).
- Choice: every `*.sqlite|*.db|*.sqlite3` is copied with `sqlite3.Connection.backup` from a read-only
  connection (`mode=ro`, best-effort `wal_checkpoint(PASSIVE)`), the copy gets `integrity_check` and
  `journal_mode=DELETE`, and `-wal`/`-shm`/`-journal` are never copied. Other top-level files use `copy2`.
  The manifest (schema 2) carries `coverage` and `omissions`: each skipped entry names a reason (size
  cap, unreadable, link, directory, not a regular file, SQLite sidecar, ownership/lock, backup folder).
  A SQLite store that is omitted or cannot be backed up makes the snapshot **failed**; a failure removes
  the half-written folder (a backup without manifest is never listed).
  `complete_data_rollback` is always `false` and the note says "NOT a full data rollback" when
  directories are omitted. No wording calls the copy a reliable rollback.
- "Verified pre-update backup": `finalize_pending_update` calls `snapshot_product_state_checked()`. When a
  product is active and the snapshot **failed**, the update does not start (no git reset, no installer):
  pending update removed, result file written with `refused: no_verified_backup`, return `False`, the
  relauncher launches the unchanged install, `jarvis update` prints a clear refusal and exits 1. The
  owner override is `jarvis update --allow-unbacked-update` (sets `DODAIOS_ALLOW_UNBACKED_UPDATE=1` for the
  detached finalizer); when used, `override_unbacked_update: true` and `backup_failure` are recorded in
  the update result. No product active means nothing to back up (not a failure).
- Frozen updates: remain without this backup (declared, section 3).
- Residual limits: a write that lands between the online backup of two different databases leaves them
  mutually inconsistent (no cross-database quiescence; the app is stopped at this point, so this is
  only a risk if something else writes); a database in a directory the process cannot open read-only
  fails the snapshot (fail closed); the backup shares the disk and directory with the data; the in-app
  restart path shows the refusal only through the update result file (no new UI string); a backup
  made by an older schema-1 manifest still restores but states "coverage unknown".
- Controls prepared, NOT RUN: `tests/unit/packaging/test_product_backup_coherence.py`
  (`test_snapshot_is_coherent_while_the_database_is_open_and_in_wal`,
  `test_manifest_lists_every_omission_with_a_reason`,
  `test_omitted_database_makes_the_snapshot_fail_and_leaves_no_half_backup`,
  `test_backup_failure_midway_is_failed_and_cleaned`,
  `test_update_is_refused_without_a_verified_backup_unless_overridden`).
  Still to do with authorization: a real writer process hammering a WAL database during a snapshot.

### H6 (HIGH): restore could mutate partially and still print "Nothing was changed"

State: **fixed in code, not run.**

- Files: `dodaios/core/product_backup.py` (`restore_product_state`, `recover_product_state`, `main`).
- Choice: before anything changes, the backup parent, the backup folder, every source file and every
  destination file are checked (linked parent/source/destination refused, resolved descendant check,
  manifest names restricted). Inputs are then STAGED as exclusive-create (`O_EXCL|O_NOFOLLOW`, 0600)
  temp files inside the verified product directory; the SHA-256 is checked while copying and again on
  the staged copy. Only then a swap under a JOURNAL (`.restore-journal-<token>.json`, rewritten before
  and after each step with the state `planned|saving|installing|installed`). On any `OSError` the journal is
  replayed backwards (previous files and sidecars moved back, installed files removed) and the temp files
  deleted. The destination's `-wal`/`-shm`/`-journal` move away together with its database. Prior files are
  kept as `<name>.before-restore`, then `.1`, `.2`, ... (never overwritten). `RestoreFailed` carries a
  report; `main` prints restored / rolled back / moved away / left untouched, and says "No data file was
  replaced" only for refusals raised before the swap. An unfinished journal (crash) blocks a new restore
  until `recover --product NAME` rolls it back.
- Residual limits: a power loss or kill between two `os.replace` calls leaves the journal for `recover`
  (no automatic recovery at start-up); a failure of the rollback itself is reported with the journal kept
  and may leave an inconsistent directory; `os.replace` and `fsync` guarantees depend on the file system
  (never exercised on Windows or network drives); sub-directories are not restored; restore holds the
  launch lock, so it cannot protect against a process that ignores the lock.
- Controls prepared, NOT RUN (same test file): `test_restore_moves_stale_wal_and_shm_away_with_the_database`
  (WAL pairing), `test_before_restore_copies_are_numbered_never_overwritten`,
  `test_mid_restore_failure_rolls_back_and_reports_precisely`,
  `test_main_never_says_nothing_changed_after_a_failed_swap`,
  `test_altered_backup_is_refused_before_any_change`,
  `test_linked_backup_parent_is_refused`, `test_linked_destination_file_is_refused_and_not_followed`,
  `test_staging_uses_exclusive_create_and_refuses_a_planted_temp_link` (linked parent/temp),
  `test_unfinished_journal_blocks_restore_and_recover_rolls_back`.

### ADR correction

Section 1 said no `it.json` exists. At this SHA the frontend has `locales/it.json` and four namespace
`it.json` files (listed in the table); only the backend lacks Italian. Corrected above.

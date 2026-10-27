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

## Block 5: environment and entry points

Static review only. **Nothing was executed**: no pytest, no test, no build, no app; only
`py_compile` and `ruff check` on the touched files.

What changed:

- `product_profile.validate_inherited` (used by the "already active, inherited" branch of
  `activate_from_argv`): the child no longer accepts the parent's variables on trust. It checks
  the announced product (`JARVIS_PRODUCT` and `JARVIS_PRODUCT_ACTIVE` agree), the credential
  service, that the data directory is absolute, named `<root>-<product>` and does not overlap the
  shared directory, that the config is that product's `dodaios.toml` inside it, and that the
  `.product` marker exists and names this product (a missing, corrupt or other-product marker is
  refused). Read-only: it does not claim and does not re-acquire the launch lock (the parent
  holds it). A product credential service with no `JARVIS_PRODUCT_ACTIVE` is refused as a
  half-inherited environment, in `activate_from_argv` and on every early entry.
- `paths.user_data_dir` no longer returns the inherited path blindly: absolute, `-<product>`
  suffix, and a marker that, if present, names the active product (else `RuntimeError`).
- `product_profile.validate_entry` + `dodaios/__main__.py`: the early dispatches (`update`,
  `--relauncher`, `--background-service` / `--persistent-server` / `--connect`, `--turn-host`)
  used to match only `argv[1]`, so `--product work --background-service` fell into the normal
  parser and `update --product work` hit the update parser. The entry flag is now found with
  `--product` removed (any position, `--product work` and `--product=work`). `update`,
  `--relauncher` and `--turn-host` validate the inherited environment before touching anything;
  the two helpers refuse a `--product` flag (the product comes from the parent's environment).
  The background-service entries pass the whole argv to the launcher, which selects or validates
  the product (it already did). `relauncher.main`, `turn_host.main` and `app_update.main` validate
  too, for `python -m <module>` starts that bypass `__main__`.
- Relaunch: the restart chain carries no `--product`; the new launcher inherits the same
  environment, validates it and reuses the same directory (no second suffix, no re-derivation).

Not verified or not done:

- Nothing above was run; the written tests (`tests/unit/core/test_product_profile_env.py`) were
  never executed. The stricter validation could reject an environment a real run produces (for
  example a legacy `Jarvis` shared directory renamed between parent and child is tolerated only
  because the check compares overlap, not the exact name).
- `update` is install-wide and takes no product lock: `dodaios update` from a shell cannot see a
  running product app's session, so it does not detect a running Work/Consumer app (limit, not
  fixed). `--product` on `update` is only validated, not used.
- Frozen builds, macOS LaunchServices and Windows branded-launcher restarts were read, not run.
  Other early or direct module entries (`--clipboard-read`, `--audio-device-probe`, the control
  CLI) were not changed.

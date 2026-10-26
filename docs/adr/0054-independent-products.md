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

The environment (`JARVIS_PRODUCT`, data dir, config, credential service) is inherited by child
processes. An unknown product name is an error, not a default.

## Consequences / limits

- Not verified: ports (both products still use the same default ports, so running them at the
  same time needs a port override), OS identity (window title, icon, Windows AppUserModelID,
  autostart entries), installers, signing, upgrades of a real installed build, macOS/Windows.
- `JARVIS_PRODUCT` set by hand without `--product` still selects the policy but shares the
  vault and directory; use the flag.
- Nothing migrates existing shared data or keys into a product; that is a separate, explicit
  step.

"""Two independent products on one machine: Work (PMI, local only) and Consumer (ADR-0049).

They share SOURCE code, never state. A product launch gets its own data directory, config file,
credential-store service and launch lock, so installing, upgrading, running or wiping one cannot
touch the other:

* ``<root>-<product>`` data directory (a sibling of the shared one, never nested in it or in the
  other product's directory), holding the product's config, credential file, stores and logs;
* a credential-store service ``dodaios-<product>`` with NO fallback to the shared ``dodaios``
  service, so a Consumer key is never readable from Work and vice versa;
* an atomic ownership marker (``.product``): a directory claimed by one product is refused by
  the other, and a corrupt or unknown marker is refused, never adopted;
* a per-product launch lock: a second launch of the SAME product is refused while the two
  products can run side by side.

Standard-library plus ``filelock``; no I/O at import. ``prepare_launch`` is called by the launcher
before ``dodaios.core.config`` is imported (the credential service is bound at import).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from filelock import FileLock, Timeout

from dodaios.core.identity_migration import is_directory_link

PRODUCTS = ("work", "consumer")
MARKER_NAME = ".product"
LAUNCH_LOCK_NAME = ".launch.lock"
CONFIG_FILE_NAME = "dodaios.toml"
SERVICE_PREFIX = "dodaios-"
PRODUCT_ENV = "JARVIS_PRODUCT"
#: Set by the launch that selected the product; children inherit it and so stay in the same
#: product (own data dir, own vault) without selecting, claiming or locking a second time.
ACTIVE_ENV = "JARVIS_PRODUCT_ACTIVE"
DATA_DIR_ENV = "JARVIS_DATA_DIR"
CONFIG_ENV = "JARVIS_CONFIG"
KEYRING_SERVICE_ENV = "JARVIS_KEYRING_SERVICE"
_SERVICE_RE = re.compile(r"^dodaios-(work|consumer)$")


class ProductError(RuntimeError):
    """The product cannot be launched with these paths; the message says why."""


class ProductConflict(ProductError):
    """A directory belongs to the other product, or two products' directories overlap."""


class AlreadyRunning(ProductError):
    """This product is already running from this data directory."""


@dataclass(frozen=True, slots=True)
class ProductPaths:
    product: str
    data_dir: Path
    config_file: Path
    keyring_service: str

    @property
    def marker(self) -> Path:
        return self.data_dir / MARKER_NAME

    @property
    def lock_file(self) -> Path:
        return self.data_dir / LAUNCH_LOCK_NAME


def validate_product(product: str | None) -> str:
    """Exactly ``work`` or ``consumer``. Anything else is an error, never a default product."""
    name = (product or "").strip().lower()
    if name not in PRODUCTS:
        raise ProductError(f"unknown product {product!r}: choose one of {', '.join(PRODUCTS)}")
    return name


def paths_for(product: str, shared_data_dir: Path) -> ProductPaths:
    """Where ``product`` lives, next to (never inside) the shared data directory."""
    name = validate_product(product)
    shared = Path(shared_data_dir)
    data_dir = shared.parent / f"{shared.name}-{name}"
    return ProductPaths(name, data_dir, data_dir / CONFIG_FILE_NAME, f"{SERVICE_PREFIX}{name}")


def _overlaps(a: Path, b: Path) -> bool:
    a, b = a.resolve(), b.resolve()
    return a == b or a in b.parents or b in a.parents


def check_no_overlap(shared_data_dir: Path) -> None:
    """The two products' directories, and the shared one, must be pairwise disjoint."""
    dirs = [paths_for(p, shared_data_dir).data_dir for p in PRODUCTS] + [Path(shared_data_dir)]
    for i, first in enumerate(dirs):
        for second in dirs[i + 1:]:
            if _overlaps(first, second):
                raise ProductConflict(f"{first} and {second} overlap: refusing to share state")


def _real(path: Path) -> Path:
    """The strict real path (every link followed, must exist), or a :class:`ProductError`."""
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProductError(f"cannot resolve {path}: {exc}") from exc


def _shared_roots() -> list[Path]:
    from dodaios.core.identity_boot import resolve_pair, user_data_dir_pair

    pair = user_data_dir_pair()
    return list({pair.current, resolve_pair(pair)})


def check_product_root(product: str, data_dir: Path, *, canonical: bool = False) -> Path:
    """Refuse a product root that is a link, or that nests with the shared root or the OTHER
    product's root (an ancestor or a descendant, after every link is resolved).

    With ``canonical`` the real path must also be exactly ``<shared-root>-<product>``, a sibling
    of the shared root. Deleting one product's root can then never reach the other's data.
    Returns the strict real path. Reads only.
    """
    name = validate_product(product)
    other = next(p for p in PRODUCTS if p != name)
    if is_directory_link(data_dir):
        raise ProductError(f"{data_dir} is a link: a product directory must be a real directory")
    real = _real(data_dir)
    shared_roots = _shared_roots()
    for shared in shared_roots:
        if _overlaps(real, shared):
            raise ProductConflict(f"{data_dir} overlaps the shared directory {shared}")
    for shared in shared_roots:
        rival = paths_for(other, shared).data_dir
        if _overlaps(real, rival):
            raise ProductConflict(f"{data_dir} overlaps the {other} product directory {rival}")
    if canonical and real not in {paths_for(name, s).data_dir.resolve() for s in shared_roots}:
        raise ProductError(
            f"{data_dir} is not the canonical {name} directory (<shared-root>-{name})"
        )
    return real


def _check_config_file(product: str, data_dir: Path, config: Path) -> None:
    """The config FILE itself must be ``dodaios.toml`` inside the real data root, and not a link:
    a link there could point at the other product's file."""
    if config.is_symlink():
        raise ProductError(f"{config} is a link: the {product} config must be a real file")
    if config.resolve() != _real(data_dir) / CONFIG_FILE_NAME:
        raise ProductError(f"{config} does not resolve inside the {product} directory {data_dir}")


def _read_owner(marker: Path) -> str | None:
    """The product recorded in ``marker``; None when absent. A bad marker is an error."""
    try:
        raw = marker.read_text(encoding="utf-8")
    except FileNotFoundError:  # no marker yet: the directory is unclaimed
        return None
    except OSError as exc:
        raise ProductError(f"cannot read {marker}: {exc.strerror or exc}") from exc
    try:
        data = json.loads(raw)
        owner = data.get("product") if isinstance(data, dict) else None
    except ValueError:  # not JSON: owner stays None and is refused just below
        owner = None
    if owner not in PRODUCTS:
        raise ProductError(f"{marker} is unreadable or names no known product: not adopting it")
    return owner


def claim(paths: ProductPaths) -> None:
    """Record ownership of the data directory atomically; refuse another product's directory.

    Two launches racing for a fresh directory cannot both win: the marker is written to a
    temporary file and published with a hard link, which fails if the marker already exists.
    """
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    with FileLock(str(paths.data_dir / ".product.lock"), timeout=10):
        owner = _read_owner(paths.marker)
        if owner is None:
            fd, tmp = tempfile.mkstemp(dir=paths.data_dir, prefix=".product.")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump({"product": paths.product}, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.link(tmp, paths.marker)
                except FileExistsError:  # another launch published its marker first: read it
                    owner = _read_owner(paths.marker)
            finally:
                os.unlink(tmp)
            owner = owner or paths.product
        if owner != paths.product:
            raise ProductConflict(
                f"{paths.data_dir} belongs to the {owner} product, not {paths.product}"
            )


def acquire_launch_lock(paths: ProductPaths) -> FileLock:
    """Hold the product's launch lock for the life of the process (release on exit)."""
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(paths.lock_file))
    try:
        lock.acquire(timeout=0)
    except Timeout as exc:
        raise AlreadyRunning(f"{paths.product} is already running from {paths.data_dir}") from exc
    return lock


def environment(paths: ProductPaths, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment of a product process (children inherit it, so they stay in the product)."""
    env = dict(os.environ if base is None else base)
    env.update({
        PRODUCT_ENV: paths.product,
        DATA_DIR_ENV: str(paths.data_dir),
        CONFIG_ENV: str(paths.config_file),
        KEYRING_SERVICE_ENV: paths.keyring_service,
    })
    return env


def prepare_launch(product: str, shared_data_dir: Path) -> tuple[ProductPaths, FileLock]:
    """Validate, claim and lock the product's directory; the caller then exports ``environment``."""
    paths = paths_for(product, shared_data_dir)
    check_no_overlap(shared_data_dir)
    claim(paths)
    return paths, acquire_launch_lock(paths)


def extract_product_flag(argv: list[str]) -> tuple[str | None, list[str]]:
    """``--product NAME`` / ``--product=NAME`` out of ``argv``: ``(name or None, argv without it)``.

    Raises :class:`ProductError` for a flag with no value or two different values. The value is
    not validated here (``validate_product`` does that), so a typo is reported, not skipped.
    """
    name: str | None = None
    rest: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--product":
            if i + 1 >= len(argv) or argv[i + 1].startswith("--"):
                raise ProductError("--product needs a value: work or consumer")
            value, i = argv[i + 1], i + 2
        elif arg.startswith("--product="):
            value, i = arg.split("=", 1)[1], i + 1
        else:
            rest.append(arg)
            i += 1
            continue
        if name is not None and name != value:
            raise ProductError(f"--product given twice with different values: {name}, {value}")
        name = value
    return name, rest


def activate_from_argv(
    argv: list[str], environ: dict[str, str] | None = None
) -> tuple[ProductPaths | None, list[str], FileLock | None]:
    """Select the product named on the command line BEFORE anything binds paths or credentials.

    Returns ``(paths, argv without --product, launch lock)``; ``paths`` is None when no product
    is involved (the unscoped app is unchanged). A child of a product process inherits
    ``JARVIS_PRODUCT_ACTIVE`` and the directory/credential environment: it stays in that product
    without claiming or locking again (the running app's own single-instance lock lives inside
    the product's directory). A different ``--product`` inside an active product, a missing
    value, an unknown name or an incomplete inherited environment are errors.
    """
    env = os.environ if environ is None else environ
    requested, rest = extract_product_flag(argv)
    active = (env.get(ACTIVE_ENV) or "").strip().lower() or None
    if active is None:
        _refuse_stray_scope(env)
    if requested is None and active is None:
        return None, rest, None
    if active is not None:
        active = validate_product(active)
    product = validate_product(requested if requested is not None else active)
    if active is not None and product != active:
        raise ProductError(f"--product {product} contradicts the running {active} product")
    service = f"{SERVICE_PREFIX}{product}"
    if active is not None:  # a child: the environment must already be the product's, consistently
        paths = validate_inherited(product, env)
        _pin_credential_service(service, env)
        return paths, rest, None
    _pin_credential_service(service, env)
    from dodaios.core.paths import user_data_dir  # after the service is pinned (branding)

    paths, lock = prepare_launch(product, user_data_dir())
    env.update(environment(paths, env))
    env[ACTIVE_ENV] = product
    return paths, rest, lock


def _refuse_stray_scope(env: Mapping[str, str]) -> None:
    """A product credential service with no announced product is a half-inherited environment.

    ``dodaios.core.branding`` would bind that vault while the data directory stayed the shared
    one: a product's keys next to the shared app's data. Refused, never repaired.
    """
    if scoped_service(env) is not None:
        raise ProductError(
            f"{KEYRING_SERVICE_ENV} names a product credential service but {ACTIVE_ENV} is not "
            "set: the inherited environment is inconsistent"
        )


def validate_inherited(product: str, env: Mapping[str, str] | None = None) -> ProductPaths:
    """Accept the environment a product's parent exported, or refuse it. Reads, never writes.

    A child of a product process does not claim or lock again (the parent holds the launch lock),
    so everything the parent promised is checked here, before ANY read or write of the vault or
    the data directory: the announced product, the data directory (named ``<root>-<product>``,
    outside the shared directory, claimed by THIS product through its ``.product`` marker), the
    config file (the product's own ``dodaios.toml`` inside that directory) and the credential
    service. Any mismatch raises :class:`ProductError` (:class:`ProductConflict` for ownership).
    """
    source = os.environ if env is None else env
    name = validate_product(product)
    service = f"{SERVICE_PREFIX}{name}"
    raw_dir = (source.get(DATA_DIR_ENV) or "").strip()
    raw_config = (source.get(CONFIG_ENV) or "").strip()
    raw_service = (source.get(KEYRING_SERVICE_ENV) or "").strip()
    declared = (source.get(PRODUCT_ENV) or "").strip().lower()
    if not raw_dir or not raw_config or not raw_service or not declared:
        raise ProductError(f"the inherited {name} environment is incomplete")
    if declared != name or raw_service != service:
        raise ProductError(
            f"the inherited {name} environment is inconsistent: {PRODUCT_ENV}={declared!r}, "
            f"{KEYRING_SERVICE_ENV}={raw_service!r}"
        )
    data_dir, config = Path(raw_dir), Path(raw_config)
    suffix = f"-{name}"
    if not data_dir.is_absolute() or not config.is_absolute():
        raise ProductError(f"the inherited {name} paths must be absolute")
    if not data_dir.name.endswith(suffix) or len(data_dir.name) == len(suffix):
        raise ProductError(f"{data_dir} is not a {name} product directory (<root>{suffix})")
    if config.name != CONFIG_FILE_NAME or config.parent.resolve() != data_dir.resolve():
        raise ProductError(f"{config} is not the {name} product's config inside {data_dir}")
    if (data_dir / MARKER_NAME).is_symlink():
        raise ProductError(f"{data_dir / MARKER_NAME} is a link: not trusting its ownership")
    check_product_root(name, data_dir, canonical=True)
    _check_config_file(name, data_dir, config)
    owner = _read_owner(data_dir / MARKER_NAME)
    if owner is None:
        raise ProductError(f"{data_dir} was never claimed by the {name} product")
    if owner != name:
        raise ProductConflict(f"{data_dir} belongs to the {owner} product, not {name}")
    return ProductPaths(name, data_dir, config, service)


def validate_entry(
    argv: list[str], environ: Mapping[str, str] | None = None, *, allow_flag: bool = True
) -> list[str]:
    """Identity check for an entry that dispatches BEFORE :func:`activate_from_argv`.

    Update, relauncher, background-service and turn-host entries start early and must not touch a
    data directory or a vault on an unchecked identity. This reads only: it never claims, locks or
    imports the config module. An inherited product environment is validated whole
    (:func:`validate_inherited`); ``--product`` (either spelling, in any position) is checked
    against it. With ``allow_flag=False`` the flag is refused: a helper takes the product from
    its parent's environment, never from its own argv. Returns ``argv`` without the flag.
    """
    env = os.environ if environ is None else environ
    requested, rest = extract_product_flag(argv)
    if requested is not None:
        if not allow_flag:
            raise ProductError(
                "--product is not accepted here: the product comes from the environment"
            )
        requested = validate_product(requested)
    active = (env.get(ACTIVE_ENV) or "").strip().lower() or None
    if active is None:
        _refuse_stray_scope(env)
        return rest
    active = validate_product(active)
    if requested is not None and requested != active:
        raise ProductError(f"--product {requested} contradicts the running {active} product")
    validate_inherited(active, env)
    return rest


def _pin_credential_service(service: str, env: dict[str, str]) -> None:
    """Bind this process to the product's credential service, or confirm it already is.

    ``dodaios.core.config`` binds the service and the data directory when it is first imported.
    Before that the service is pinned here. After it, activating again (``__main__`` selects the
    product, then the launcher is handed the same environment) is safe ONLY when everything
    already bound is the requested product's: credential service, data directory and legacy
    fallback. Anything else is refused, never rebound.
    """
    import sys

    config = sys.modules.get("dodaios.core.config")
    if config is None:
        env[KEYRING_SERVICE_ENV] = service
        from dodaios.core import branding

        branding.pin_product_service(service)
        return
    data_dir = env.get(DATA_DIR_ENV)
    bound_dir = getattr(config, "DATA_DIR", None)
    matches = (
        getattr(config, "KEYRING_SERVICE", None) == service
        and not getattr(config, "LEGACY_KEYRING_SERVICES", ("shared",))
        and env.get(KEYRING_SERVICE_ENV) == service
        and bool(data_dir)
        and bound_dir is not None
        and Path(str(bound_dir)).resolve() == Path(str(data_dir)).resolve()
    )
    if not matches:
        raise ProductError(
            "the product was selected after the credential store was bound to something else"
        )


def scoped_service(env: Mapping[str, str] | None = None) -> str | None:
    """The product credential service named by the environment, or None (shared service).

    Anything but ``dodaios-work`` / ``dodaios-consumer`` is ignored: an arbitrary service name
    must not let a process read another application's credentials.
    """
    value = ((env if env is not None else os.environ).get(KEYRING_SERVICE_ENV) or "").strip()
    return value if _SERVICE_RE.match(value) else None


__all__ = [
    "ACTIVE_ENV",
    "AlreadyRunning",
    "PRODUCTS",
    "ProductConflict",
    "ProductError",
    "ProductPaths",
    "acquire_launch_lock",
    "activate_from_argv",
    "check_no_overlap",
    "check_product_root",
    "claim",
    "environment",
    "extract_product_flag",
    "paths_for",
    "prepare_launch",
    "scoped_service",
    "validate_entry",
    "validate_inherited",
    "validate_product",
]

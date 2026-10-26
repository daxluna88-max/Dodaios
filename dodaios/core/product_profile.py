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

PRODUCTS = ("work", "consumer")
MARKER_NAME = ".product"
LAUNCH_LOCK_NAME = ".launch.lock"
SERVICE_PREFIX = "dodaios-"
PRODUCT_ENV = "JARVIS_PRODUCT"
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
    return ProductPaths(name, data_dir, data_dir / "dodaios.toml", f"{SERVICE_PREFIX}{name}")


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


def scoped_service(env: Mapping[str, str] | None = None) -> str | None:
    """The product credential service named by the environment, or None (shared service).

    Anything but ``dodaios-work`` / ``dodaios-consumer`` is ignored: an arbitrary service name
    must not let a process read another application's credentials.
    """
    value = ((env if env is not None else os.environ).get(KEYRING_SERVICE_ENV) or "").strip()
    return value if _SERVICE_RE.match(value) else None


__all__ = [
    "AlreadyRunning",
    "PRODUCTS",
    "ProductConflict",
    "ProductError",
    "ProductPaths",
    "acquire_launch_lock",
    "check_no_overlap",
    "claim",
    "environment",
    "paths_for",
    "prepare_launch",
    "scoped_service",
    "validate_product",
]

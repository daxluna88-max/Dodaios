"""The phone companion's file store: resume, integrity, quotas, isolation, crash repair, expiry."""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

import pytest

from dodaios.phone import store as store_module
from dodaios.phone.store import NotFound, TransferError, TransferStore

OWNER, DEVICE = "owner", "dev1"


def make(store: TransferStore, data: bytes = b"abcdef", **kwargs) -> str:
    return store.create(OWNER, DEVICE, "video.mp4", len(data), hashlib.sha256(data).hexdigest(),
                        **kwargs)


def test_restart_resume_and_download(tmp_path: Path) -> None:
    store = TransferStore(tmp_path, chunk_size=3)
    key = make(store)
    assert store.append(OWNER, key, 0, b"abc") == 3
    store = TransferStore(tmp_path, chunk_size=3)  # the process restarted
    assert store.status(OWNER, key)["offset"] == 3
    with pytest.raises(TransferError, match="offset"):
        store.append(OWNER, key, 0, b"abc")
    with pytest.raises(TransferError):
        store.read(OWNER, key)  # not downloadable before finish
    store.append(OWNER, key, 3, b"def")
    assert store.finish(OWNER, key)["ready"]
    assert store.read(OWNER, key) + store.read(OWNER, key, 3) == b"abcdef"
    assert store.finish(OWNER, key)["ready"]  # idempotent


def test_other_owners_see_nothing(tmp_path: Path) -> None:
    store = TransferStore(tmp_path)
    key = make(store)
    for action in (lambda: store.status("other", key), lambda: store.finish("other", key),
                   lambda: store.append("other", key, 0, b"abc"),
                   lambda: store.delete("other", key), lambda: store.open_ready("other", key)):
        with pytest.raises(NotFound):
            action()
    assert store.list("other") == []
    for name in ("../x", "/x", "a\\b", "bad\nname", "..", ""):
        with pytest.raises(TransferError):
            store.create(OWNER, DEVICE, name, 0, hashlib.sha256(b"").hexdigest())
    with pytest.raises(TransferError, match="invalid transfer id"):
        store.status(OWNER, "../../etc/passwd")


def test_integrity_and_quota(tmp_path: Path) -> None:
    store = TransferStore(tmp_path, max_file=6, quota=6, chunk_size=3)
    key = make(store)
    with pytest.raises(TransferError, match="quota"):
        make(store)
    with pytest.raises(TransferError):
        store.append(OWNER, key, 0, b"abcd")  # bigger than a chunk
    store.append(OWNER, key, 0, b"xxx")
    store.append(OWNER, key, 3, b"xxx")
    with pytest.raises(TransferError, match="checksum"):
        store.finish(OWNER, key)
    with pytest.raises(TransferError):
        store.read(OWNER, key)
    store.delete(OWNER, key)  # releases the reservation
    make(store)
    with pytest.raises(TransferError, match="size"):
        store.create(OWNER, DEVICE, "big", 7, "0" * 64)


def test_uncommitted_tail_is_repaired(tmp_path: Path) -> None:
    store = TransferStore(tmp_path, chunk_size=3)
    key = make(store)
    store.append(OWNER, key, 0, b"abc")
    with (tmp_path / (key + ".blob")).open("ab") as handle:
        handle.write(b"crash tail")
    store.append(OWNER, key, 3, b"def")
    assert store.finish(OWNER, key)["ready"]
    assert store.read(OWNER, key) == b"abc"[:3] and store.read(OWNER, key, 3) == b"def"


def test_empty_file(tmp_path: Path) -> None:
    store = TransferStore(tmp_path)
    key = make(store, b"")
    store.finish(OWNER, key)
    assert store.read(OWNER, key) == b""


def test_concurrent_writers_cannot_overwrite_a_chunk(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    store = TransferStore(tmp_path, chunk_size=3)
    key = make(store)

    def write(_: int) -> object:
        try:
            return TransferStore(tmp_path, chunk_size=3).append(OWNER, key, 0, b"abc")
        except TransferError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(write, range(4)))
    assert results.count(3) == 1 and results.count("conflict") == 3
    assert store.status(OWNER, key)["offset"] == 3


def test_a_long_hash_does_not_block_other_transfers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = TransferStore(tmp_path, chunk_size=3)
    slow, other = make(store), make(store, b"zzz")
    store.append(OWNER, slow, 0, b"abc")
    store.append(OWNER, slow, 3, b"def")
    started, release = threading.Event(), threading.Event()
    real = hashlib.sha256

    class Slow:
        def __init__(self) -> None:
            self._inner = real()

        def update(self, block: bytes) -> None:
            started.set()
            release.wait(10)
            self._inner.update(block)

        def hexdigest(self) -> str:
            return self._inner.hexdigest()

    monkeypatch.setattr(store_module.hashlib, "sha256", Slow)
    worker = threading.Thread(target=store.finish, args=(OWNER, slow))
    worker.start()
    assert started.wait(5)
    # While one file is being hashed, the database and other transfers stay usable.
    started_at = time.monotonic()
    assert store.status(OWNER, other)["offset"] == 0
    assert store.append(OWNER, other, 0, b"zzz") == 3
    assert time.monotonic() - started_at < 5
    release.set()
    worker.join(10)
    assert store.status(OWNER, slow)["ready"]


def test_sweep_expires_old_transfers_and_removes_orphans(tmp_path: Path) -> None:
    store = TransferStore(tmp_path, chunk_size=3, incomplete_ttl_s=100, ready_ttl_s=1000)
    stale, done, fresh = make(store), make(store), make(store)
    store.append(OWNER, done, 0, b"abc")
    store.append(OWNER, done, 3, b"def")
    store.finish(OWNER, done)
    orphan = tmp_path / ("f" * 32 + ".blob")
    orphan.write_bytes(b"x")
    long_ago = time.time() - 4000
    import os

    os.utime(orphan, (long_ago, long_ago))
    now = time.time()
    # Backdate the rows rather than sleeping.
    with store._db() as db:
        db.execute("UPDATE transfers SET updated_at=? WHERE id=?", (now - 500, stale))
        db.execute("UPDATE transfers SET updated_at=? WHERE id=?", (now - 500, done))
    result = store.sweep(now)
    assert result["expired"] == 1 and result["orphan_blobs"] == 1
    assert not orphan.exists() and not (tmp_path / (stale + ".blob")).exists()
    assert store.status(OWNER, done)["ready"] and store.status(OWNER, fresh)["offset"] == 0
    store.sweep(now + 2000)  # a finished file expires too, and its quota is released
    assert store.list(OWNER) == []


def test_delete_for_device_drops_only_its_unfinished_uploads(tmp_path: Path) -> None:
    store = TransferStore(tmp_path, chunk_size=3)
    unfinished = make(store)
    finished = make(store)
    store.append(OWNER, finished, 0, b"abc")
    store.append(OWNER, finished, 3, b"def")
    store.finish(OWNER, finished)
    assert store.delete_for_device(DEVICE) == 1
    assert [row["id"] for row in store.list(OWNER)] == [finished]
    with pytest.raises(NotFound):
        store.status(OWNER, unfinished)


def test_a_guard_gets_the_last_word_before_every_mutation(tmp_path: Path) -> None:
    from dodaios.phone.store import Revoked

    store = TransferStore(tmp_path, chunk_size=3)
    with pytest.raises(Revoked):
        make(store, guard=lambda: False)
    assert store.list(OWNER) == []
    key = make(store)
    with pytest.raises(Revoked):
        store.append(OWNER, key, 0, b"abc", guard=lambda: False)
    assert store.status(OWNER, key)["offset"] == 0
    store.append(OWNER, key, 0, b"abc", guard=lambda: True)
    store.append(OWNER, key, 3, b"def")
    with pytest.raises(Revoked):
        store.finish(OWNER, key, guard=lambda: False)  # revoked while the file was hashed
    assert store.status(OWNER, key)["ready"] is False
    assert store.finish(OWNER, key, guard=lambda: True)["ready"] is True

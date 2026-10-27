"""Memory proposals: what the learning review wants to keep, waiting for the owner (ADR-0061).

A proposal is a note in state ``proposed``. It lives only in this store: it is NOT in the
notebooks, so it is not part of any prompt, recall or snapshot, and nothing sends it anywhere.
Only :meth:`ProposalStore.approve` (one note, by id) writes it to the notebook, optionally with
the owner's edited text. :meth:`reject` keeps a hash of the rejected content so the same proposal
is not made again. There is deliberately no bulk approve and no approve-all.

Each record is bound to one product (``work`` / ``consumer`` / ``shared`` when none is set); a
store never lists, approves or rejects another product's record.

Provenance: ``local_only`` is True for every proposal. ``provenance`` is ``restricted`` when the
conversation material carried a positive local-only signal and ``unlabelled`` otherwise (a
background review has no per-turn label; absence of a label is never read as "shareable").
The notebook itself has no per-entry label, so the label is kept on the record and shown, and
is NOT enforced after approval (ADR-0061, limits).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

log = logging.getLogger(__name__)

FILE_NAME: Final[str] = ".memory-proposals.json"
PROPOSED: Final[str] = "proposed"
APPROVED: Final[str] = "approved"
REJECTED: Final[str] = "rejected"
#: Open proposals kept per product; further ones are dropped (logged), never queued silently.
MAX_PENDING: Final[int] = 100
#: Decided records and rejected hashes kept.
MAX_DECIDED: Final[int] = 200
MAX_REJECTED_HASHES: Final[int] = 500
SHARED: Final[str] = "shared"


def current_product_name() -> str:
    from dodaios.brain.engine_policy import current_product

    return current_product() or SHARED


def content_hash(target: str, operation: str, entry_id: str, text: str) -> str:
    """Stable hash of what a proposal would change (whitespace and case do not matter)."""
    norm = " ".join((text or "").lower().split())
    raw = "\x1f".join((target, operation, entry_id or "", norm))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


@dataclass
class Proposal:
    id: str
    product: str
    state: str
    target: str
    operation: str
    text: str
    entry_id: str = ""
    before: str = ""
    evidence: str = ""
    importance: int = 5
    source: str = ""
    seat: dict[str, str] = field(default_factory=dict)
    local_only: bool = True
    provenance: str = "unlabelled"
    created: float = 0.0
    decided: float = 0.0
    edited: bool = False
    hash: str = ""

    def public(self) -> dict[str, Any]:
        return asdict(self)


class ProposalError(Exception):
    """A decision that cannot be made (unknown id, wrong state, refused text)."""

    def __init__(self, message: str, *, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


class ProposalStore:
    """JSON file next to the notebooks, one per product folder."""

    def __init__(self, folder: Path, *, product: str | None = None) -> None:
        self._path = Path(folder) / FILE_NAME
        self.product = product or current_product_name()
        self._lock = threading.Lock()

    # -- file ------------------------------------------------------------

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"proposals": [], "rejected": {}}
        except (OSError, ValueError):
            log.warning("memory proposals: unreadable file %s, treated as empty", self._path)
            return {"proposals": [], "rejected": {}}
        if not isinstance(data, dict):
            return {"proposals": [], "rejected": {}}
        data.setdefault("proposals", [])
        data.setdefault("rejected", {})
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".mp-", suffix=".tmp", dir=str(self._path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(data, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self._path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                log.debug("memory proposals: temp file already gone")
            raise

    @staticmethod
    def _load(raw: dict[str, Any]) -> Proposal | None:
        try:
            return Proposal(**raw)
        except TypeError:
            return None

    def _mine(self, data: dict[str, Any]) -> list[Proposal]:
        out = []
        for raw in data["proposals"]:
            item = self._load(raw)
            if item is not None and item.product == self.product:
                out.append(item)
        return out

    # -- API -------------------------------------------------------------

    def propose(
        self,
        *,
        target: str,
        operation: str,
        text: str,
        entry_id: str = "",
        before: str = "",
        evidence: str = "",
        importance: int = 5,
        source: str = "",
        seat: dict[str, str] | None = None,
        provenance: str = "unlabelled",
    ) -> Proposal | None:
        """Add a proposal; ``None`` if rejected before, already open, or the queue is full."""
        digest = content_hash(target, operation, entry_id, text)
        with self._lock:
            data = self._read()
            if digest in data["rejected"].get(self.product, {}):
                log.info("memory proposals: identical to a rejected one, not proposed again")
                return None
            mine = self._mine(data)
            if any(p.hash == digest and p.state == PROPOSED for p in mine):
                return None
            if sum(1 for p in mine if p.state == PROPOSED) >= MAX_PENDING:
                log.warning("memory proposals: %d open, dropping a new one", MAX_PENDING)
                return None
            item = Proposal(
                id=uuid.uuid4().hex[:12],
                product=self.product,
                state=PROPOSED,
                target=target,
                operation=operation,
                text=text,
                entry_id=entry_id,
                before=before,
                evidence=evidence,
                importance=int(importance),
                source=source,
                seat=dict(seat or {}),
                local_only=True,
                provenance=provenance,
                created=time.time(),
                hash=digest,
            )
            data["proposals"].append(asdict(item))
            self._write(data)
            return item

    def items(self, *, state: str | None = PROPOSED) -> list[Proposal]:
        with self._lock:
            items = self._mine(self._read())
        if state is not None:
            items = [p for p in items if p.state == state]
        return sorted(items, key=lambda p: -p.created)

    def _find(self, data: dict[str, Any], proposal_id: str) -> tuple[int, Proposal]:
        for index, raw in enumerate(data["proposals"]):
            item = self._load(raw)
            if item is not None and item.id == proposal_id and item.product == self.product:
                return index, item
        raise ProposalError("No such proposal for this product.", status=404)

    def approve(
        self,
        proposal_id: str,
        apply: Callable[..., Any],
        *,
        text: str | None = None,
    ) -> Proposal:
        """Approve ONE proposal. ``text`` is the owner's edit (edit + approve).

        ``apply`` is the notebook's ``apply``. The record becomes ``approved`` only after the
        write succeeded; a refusal leaves it ``proposed`` and raises :class:`ProposalError`.
        """
        from dodaios.memory.learning.guard import refusal

        with self._lock:
            data = self._read()
            index, item = self._find(data, proposal_id)
            if item.state != PROPOSED:
                raise ProposalError(f"This proposal is already {item.state}.")
            final = " ".join(text.split()) if text is not None else item.text
            edited = text is not None and final != item.text
            if item.operation != "remove":
                if not final:
                    raise ProposalError("The note is empty.", status=422)
                reason = refusal(final)
                if reason:
                    raise ProposalError(f"Not accepted: {reason}", status=422)
            try:
                apply(
                    target=item.target,
                    operation=item.operation,
                    text=final if item.operation != "remove" else "",
                    entry_id=item.entry_id,
                    importance=item.importance,
                    origin="review",
                    evidence=item.evidence,
                    source="approved by owner" + (" (edited)" if edited else ""),
                    expected=item.before if item.operation != "add" else None,
                    seat=item.seat,
                )
            except (ValueError, OSError) as exc:
                raise ProposalError(f"Not saved: {exc}") from exc
            item.state = APPROVED
            item.decided = time.time()
            item.edited = edited
            if edited:
                item.text = final
            data["proposals"][index] = asdict(item)
            self._trim(data)
            self._write(data)
            return item

    def reject(self, proposal_id: str) -> Proposal:
        """Reject ONE proposal and remember its hash so it is not proposed again."""
        with self._lock:
            data = self._read()
            index, item = self._find(data, proposal_id)
            if item.state != PROPOSED:
                raise ProposalError(f"This proposal is already {item.state}.")
            item.state = REJECTED
            item.decided = time.time()
            hashes = data["rejected"].setdefault(self.product, {})
            hashes[item.hash] = item.decided
            if len(hashes) > MAX_REJECTED_HASHES:
                for old in sorted(hashes, key=hashes.get)[: len(hashes) - MAX_REJECTED_HASHES]:
                    del hashes[old]
            # The rejected text itself is not kept: only its hash.
            item.text = ""
            item.evidence = ""
            item.before = ""
            data["proposals"][index] = asdict(item)
            self._trim(data)
            self._write(data)
            return item

    def _trim(self, data: dict[str, Any]) -> None:
        decided = [
            (i, raw.get("decided", 0.0))
            for i, raw in enumerate(data["proposals"])
            if raw.get("state") in (APPROVED, REJECTED)
        ]
        if len(decided) > MAX_DECIDED:
            drop = {i for i, _ in sorted(decided, key=lambda t: t[1])[: len(decided) - MAX_DECIDED]}
            data["proposals"] = [r for i, r in enumerate(data["proposals"]) if i not in drop]

    def forget_all_for_product(self) -> int:
        """Drop every record and rejected hash of THIS product (used by a full memory delete)."""
        with self._lock:
            data = self._read()
            keep = [r for r in data["proposals"] if r.get("product") != self.product]
            removed = len(data["proposals"]) - len(keep)
            data["proposals"] = keep
            data["rejected"].pop(self.product, None)
            self._write(data)
            return removed


def provenance_of(texts: list[str]) -> str:
    """``restricted`` on a positive local-only signal in the material, else ``unlabelled``."""
    # Positive-evidence tagging only (block 13): "unlabelled" is NOT a permission, and the remote
    # boundary treats it as unavailable.
    from dodaios.brain.provenance import local_only_signal

    try:
        if any(local_only_signal(t) for t in texts):
            return "restricted"
    except Exception:  # noqa: BLE001 - a label failure must read as the safe default
        log.debug("memory proposals: provenance check failed", exc_info=True)
    return "unlabelled"

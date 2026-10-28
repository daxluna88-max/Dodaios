"""Editable memory entries inside the existing Markdown memory page.

Each Markdown notebook remains its source of truth. Stable ids survive
corrections; legacy date-heading entries remain readable during migration.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass

_MARKER = re.compile(r"^<!-- memory-entry: (.+) -->$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class Entry:
    id: str
    text: str
    importance: int = 5
    revision: int = 0
    origin: str = "agent"
    #: ADR-0061 block 17 (H2). Entry-level provenance. An entry with no stored label (every entry
    #: written before this field existed, every agent write) is local-only and UNAVAILABLE.
    local_only: bool = True
    provenance: str = "unavailable"


#: The only label pair that lets an entry reach a remote destination.
SHAREABLE_PROVENANCE = "unrestricted"


def remote_shareable(entry: object) -> bool:
    """True only for an entry EXPLICITLY marked remote-shareable (``local_only`` is False AND the
    typed provenance is ``unrestricted``). Missing, unknown or contradictory labels are False."""
    return (
        getattr(entry, "local_only", True) is False
        and str(getattr(entry, "provenance", "unavailable")) == SHAREABLE_PROVENANCE
    )


def visible_entries(entries: list[Entry], *, remote: bool) -> list[Entry]:
    """What a destination may be given: everything locally, shareable entries only remotely."""
    return [e for e in entries if remote_shareable(e)] if remote else list(entries)


def normalise_labels(local_only: object, provenance: object) -> tuple[bool, str]:
    """Collapse a stored/supplied label pair. Only ``local_only`` literally False together with
    provenance ``unrestricted`` is shareable; everything else is local-only (``restricted`` when
    that was said, ``unavailable`` otherwise)."""
    prov = str(provenance or "").strip().lower()
    if local_only is False and prov == SHAREABLE_PROVENANCE:
        return False, SHAREABLE_PROVENANCE
    return True, ("restricted" if prov == "restricted" else "unavailable")


def identity(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def readable_document(text: str) -> str:
    """The user-facing Markdown, without frontmatter and entry bookkeeping."""
    text = re.sub(r"\A\ufeff?---\r?\n.*?\r?\n---(?:\r?\n|$)", "", text, flags=re.DOTALL)
    text = re.sub(r"^<!-- memory-entry: [^\r\n]* -->\r?\n?", "", text, flags=re.MULTILINE)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse(body: str) -> list[Entry]:
    marks = list(_MARKER.finditer(body))
    if not marks:
        chunks = re.split(r"(?m)(?=^## \d{4}-\d{2}-\d{2}\s*$)", body)
        return [
            Entry(identity(s), s.strip(), revision=i) for i, s in enumerate(chunks) if s.strip()
        ]
    entries: list[Entry] = []
    prefix = body[: marks[0].start()].strip()
    if prefix:
        entries.extend(parse(prefix))
    for i, mark in enumerate(marks):
        # Invalid metadata must fail the write, never discard existing content.
        meta = json.loads(mark.group(1))
        local_only, provenance = normalise_labels(
            meta.get("local_only", True), meta.get("provenance")
        )
        text = body[mark.end() : marks[i + 1].start() if i + 1 < len(marks) else len(body)].strip()
        entries.append(
            Entry(
                str(meta["id"]),
                text,
                int(meta.get("importance", 5)),
                int(meta.get("revision", i)),
                str(meta.get("origin", "agent")),
                local_only,
                provenance,
            )
        )
    return entries


def render(entries: list[Entry]) -> str:
    parts = []
    for entry in entries:
        meta = asdict(entry)
        meta.pop("text")
        # Default labels (local-only, unavailable) are not written: an unlabelled entry stays
        # byte-identical to the pre-label format and reads back as local-only.
        if meta.get("local_only", True) is not False:
            meta.pop("local_only", None)
        if meta.get("provenance") == "unavailable":
            meta.pop("provenance", None)
        parts.append(
            "<!-- memory-entry: " + json.dumps(meta, ensure_ascii=True) + " -->\n" + entry.text
        )
    return "\n\n".join(parts) + "\n"


def change(
    entries: list[Entry],
    text: str,
    *,
    operation: str = "add",
    entry_id: str = "",
    old_text: str = "",
    importance: int = 5,
    origin: str = "agent",
    local_only: bool = True,
    provenance: str = "unavailable",
) -> list[Entry]:
    if operation not in {"add", "replace", "remove"}:
        raise ValueError("operation must be add, replace or remove")
    if operation != "remove" and not text.strip():
        raise ValueError("text is required")
    if "<!-- memory-entry:" in text:
        raise ValueError("memory metadata cannot be supplied as content")
    # The labels belong to the text being written now: a replace never inherits the old entry's.
    local_only, provenance = normalise_labels(local_only, provenance)
    revision = max((e.revision for e in entries), default=0) + 1
    if operation == "add":
        if any(e.text.strip() == text.strip() for e in entries):
            return entries
        added = Entry(
            identity(text), text.strip(), importance, revision, origin, local_only, provenance
        )
        return [*entries, added]
    found = [
        e
        for e in entries
        if (e.id == entry_id if entry_id else bool(old_text) and old_text in e.text)
    ]
    if len(found) != 1:
        raise ValueError("identify exactly one existing memory by entry_id or unique old_text")
    target = found[0]
    return [
        (
            Entry(e.id, text.strip(), importance, revision, origin, local_only, provenance)
            if e.id == target.id
            else e
        )
        for e in entries
        if operation != "remove" or e.id != target.id
    ]


def select_entries(
    entries: list[Entry], *, max_chars: int, remote: bool = False
) -> tuple[list[Entry], int]:
    """Select whole entries by importance and recency; keep the remainder on disk.

    ``remote=True`` first drops every entry not explicitly marked remote-shareable.
    """
    entries = visible_entries(entries, remote=remote)
    selected: list[Entry] = []
    omitted = 0
    used = 0
    for entry in sorted(entries, key=lambda e: (-e.importance, -e.revision, e.id)):
        line = f"[{entry.id}] {entry.text}"
        if used + len(line) + 2 <= max_chars:
            selected.append(entry)
            used += len(line) + 2
        else:
            omitted += 1
    return selected, omitted


def briefing(entries: list[Entry], *, max_chars: int, remote: bool = False) -> str:
    """Render complete entries within the prompt budget, without truncating facts."""
    selected, omitted = select_entries(entries, max_chars=max_chars, remote=remote)
    lines = [f"[{entry.id}] {entry.text}" for entry in selected]
    if omitted:
        lines.append(
            f"{omitted} more entries remain on disk; use society_memory_recall to retrieve them."
        )
    return "\n\n".join(lines)

"""The phone's "to do" list: the agent's own reminders note, read (and ticked) from the vault.

The agent keeps reminders in ``<vault>/projects/promemoria.md``. The phone reads that one fixed
file, never a path it is given, and can only flip the checkbox of a line it just listed.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

NOTE = Path("projects") / "promemoria.md"
MAX_BYTES = 256 * 1024
_ITEM = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(?:\[( |x|X)\]\s+)?(.+?)\s*$")
_HEAD = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")


def note_path(vault: Path) -> Path:
    return Path(vault) / NOTE


def _read(path: Path) -> list[str] | None:
    try:
        if path.is_symlink() or path.stat().st_size > MAX_BYTES:
            return None
        return path.read_text("utf-8").split("\n")
    except OSError:
        return None


def _tag(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]  # noqa: S324 - a line fingerprint, not security


def list_items(vault: Path) -> dict[str, Any]:
    lines = _read(note_path(vault))
    if lines is None:
        return {"available": False, "items": []}
    items: list[dict[str, Any]] = []
    group = ""
    start = 1 if lines and lines[0].strip() == "---" else 0
    if start:  # skip the frontmatter block
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                start = i + 1
                break
        else:
            start = 0
    for number in range(start, len(lines)):
        line = lines[number]
        head = _HEAD.match(line)
        if head:
            group = head.group(1)
            continue
        item = _ITEM.match(line)
        if item:
            box, text = item.group(2), item.group(3)
            items.append({
                "line": number, "tag": _tag(line), "text": text, "group": group,
                "done": box in ("x", "X"), "checkbox": box is not None,
            })
    return {"available": True, "items": items}


class Stale(Exception):
    """The note changed since the phone listed it."""


def set_done(vault: Path, line: int, tag: str, done: bool) -> None:
    path = note_path(vault)
    lines = _read(path)
    if lines is None or not 0 <= line < len(lines) or _tag(lines[line]) != tag:
        raise Stale
    m = _ITEM.match(lines[line])
    if m is None:
        raise Stale
    mark = "x" if done else " "
    rest = lines[line][m.start(3):]
    prefix = lines[line][: m.start(3)]
    if m.group(2) is not None:  # "- [ ] text": flip the box in place
        lines[line] = re.sub(r"\[( |x|X)\]", f"[{mark}]", prefix, count=1) + rest
    else:  # "- text": add a box
        lines[line] = prefix + f"[{mark}] " + rest
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text("\n".join(lines), "utf-8")
    os.replace(tmp, path)

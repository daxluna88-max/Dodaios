#!/usr/bin/env bash
# One-command update of the Jarvis pilot install on the VPS.
# Fetches MANIFEST (patch name + sha256, in apply order), skips patches that are
# already applied, verifies and applies the rest, then restarts the server.
set -euo pipefail
BRANCH="${JARVIS_PILOT_BRANCH:-claude/it-complete}"
BASE="https://raw.githubusercontent.com/daxluna88-max/Dodaios/${BRANCH}/scripts/pilot"
TREE="$HOME/.dodaios"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

curl -fsSL "$BASE/MANIFEST" -o "$TMP/MANIFEST"
cd "$TREE"
applied=0
while read -r name sum; do
  [ -n "$name" ] || continue
  curl -fsSL "$BASE/$name" -o "$TMP/$name"
  got="$(sha256sum "$TMP/$name" | cut -d' ' -f1)"
  if [ "$got" != "$sum" ]; then echo "STOP: sha256 mismatch for $name"; exit 1; fi
  if git apply --check -R "$TMP/$name" 2>/dev/null; then echo "= $name (already applied)"; continue; fi
  if ! git apply --check "$TMP/$name" 2>/dev/null; then echo "STOP: $name neither applied nor applicable"; exit 1; fi
  git apply "$TMP/$name"; echo "+ $name applied"; applied=$((applied+1))
done < "$TMP/MANIFEST"

if [ "$applied" -gt 0 ] || [ "${1:-}" = "--restart" ]; then
  pkill -x jarvis || true
  sleep 2
  cd "$HOME"
  set -a; . "$HOME/.dodaios.env"; set +a
  nohup jarvis serve > "$HOME/jarvis-serve.log" 2>&1 &
  sleep 6
  if pgrep -x jarvis >/dev/null; then echo "RESTARTED: jarvis running"; else echo "FAIL: jarvis not running"; tail -20 "$HOME/jarvis-serve.log"; exit 1; fi
else
  echo "Nothing to apply; server not restarted (use --restart to force)."
fi

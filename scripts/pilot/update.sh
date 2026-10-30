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
names=(); sums=()
while read -r name sum; do
  [ -n "$name" ] || continue
  curl -fsSL "$BASE/$name" -o "$TMP/$name"
  got="$(sha256sum "$TMP/$name" | cut -d' ' -f1)"
  if [ "$got" != "$sum" ]; then echo "STOP: sha256 mismatch for $name"; exit 1; fi
  names+=("$name"); sums+=("$sum")
done < "$TMP/MANIFEST"

# Patches build on each other, so only the newest applied one reverse-checks
# cleanly: find it, treat everything up to it as applied, apply the rest.
last=-1
for ((i=${#names[@]}-1; i>=0; i--)); do
  if git apply --check -R "$TMP/${names[$i]}" 2>/dev/null; then last=$i; break; fi
done
for ((i=0; i<=last; i++)); do echo "= ${names[$i]} (already applied)"; done
applied=0
for ((i=last+1; i<${#names[@]}; i++)); do
  if ! git apply --check "$TMP/${names[$i]}" 2>/dev/null; then echo "STOP: ${names[$i]} not applicable"; exit 1; fi
  git apply "$TMP/${names[$i]}"; echo "+ ${names[$i]} applied"; applied=$((applied+1))
done

# Pilot env defaults: written once, never overwritten if already present.
ENVF="$HOME/.dodaios.env"
ensure_env() {
  if grep -q "^$1=" "$ENVF" 2>/dev/null; then echo "= env $1 (already set)"; else
    echo "$1=$2" >> "$ENVF"; echo "+ env $1 set"; applied=$((applied+1)); fi
}
ensure_env JARVIS__BRAIN__WORKER__PROVIDER openrouter
ensure_env JARVIS__BRAIN__WORKER__MODEL qwen/qwen3.8-flash

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

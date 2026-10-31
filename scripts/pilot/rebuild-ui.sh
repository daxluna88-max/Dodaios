#!/usr/bin/env bash
# Rebuild the web UI of the Jarvis pilot and install it where the server reads it.
#
# MANUAL ONLY. autodeploy.sh must never call this: the build needs about 5 GB of RAM and minutes of CPU
# on a VPS with 8 GB, and it replaces files the running server serves.
#
# Default is a DRY RUN that only prints what would happen. Pass --yes to execute.
#   bash scripts/pilot/rebuild-ui.sh          # plan only, changes nothing
#   bash scripts/pilot/rebuild-ui.sh --yes    # build, install, restart, verify
#
# Why a copy step exists: the server runs the INSTALLED tree (~/.dodaios/dodaios), not the checkout
# (~/Dodaios). A UI built in the checkout is invisible until `dist` is copied into the installed tree.
# Why the assets are read from index.html: the bundle names are hashed, guessing them gave a wrong check once.
set -euo pipefail

BRANCH="${JARVIS_PILOT_BRANCH:-claude/it-complete}"
SRC="${JARVIS_PILOT_SRC:-$HOME/Dodaios}"
TREE="${JARVIS_PILOT_TREE:-$HOME/.dodaios}"
PORT="${JARVIS_PORT:-47821}"
MIN_MEM_MB="${JARVIS_UI_MIN_MEM_MB:-5600}"
MIN_DISK_MB="${JARVIS_UI_MIN_DISK_MB:-2000}"
STAMP="$(date +%Y%m%d-%H%M%S)"
FRONT="$SRC/dodaios/ui/web/frontend"
BUILT="$SRC/dodaios/ui/web/dist"
LIVE="$TREE/dodaios/ui/web/dist"

EXEC=0
[ "${1:-}" = "--yes" ] && EXEC=1

say() { printf '%s\n' "$*"; }
run() {
  if [ "$EXEC" -eq 1 ]; then say "+ $*"; "$@"; else say "would run: $*"; fi
}
die() { say "STOP: $*"; exit 1; }

[ "$EXEC" -eq 1 ] && say "MODE: execute" || say "MODE: dry run (nothing is changed; pass --yes to execute)"

# --- Preconditions (checked in both modes) ---------------------------------------------------------
[ -d "$SRC/.git" ] || die "no checkout at $SRC"
[ -d "$TREE/dodaios/ui/web" ] || die "no installed tree at $TREE"
command -v node >/dev/null || die "node is not installed"
command -v npm >/dev/null || die "npm is not installed"

mem_mb=$(awk '/MemAvailable/ {printf "%d", $2/1024}' /proc/meminfo)
disk_mb=$(df -Pm "$SRC" | awk 'NR==2 {print $4}')
say "free RAM: ${mem_mb} MB (needs ${MIN_MEM_MB}); free disk: ${disk_mb} MB (needs ${MIN_DISK_MB})"
[ "$mem_mb" -ge "$MIN_MEM_MB" ] || die "not enough free RAM for the build (Node runs out of memory below the limit). Stop other work, or stop the server first: pkill -x jarvis"
[ "$disk_mb" -ge "$MIN_DISK_MB" ] || die "not enough free disk"

if [ -n "$(git -C "$SRC" status --porcelain)" ]; then die "the checkout $SRC has local changes; refusing to build over them"; fi

before="$(git -C "$SRC" rev-parse --short HEAD)"
run git -C "$SRC" fetch origin "$BRANCH"
run git -C "$SRC" checkout "$BRANCH"
run git -C "$SRC" merge --ff-only "origin/$BRANCH"
if [ "$EXEC" -eq 1 ]; then SHA="$(git -C "$SRC" rev-parse --short HEAD)"; else SHA="$(git -C "$SRC" rev-parse --short "origin/$BRANCH" 2>/dev/null || echo unknown)"; fi
say "checkout was at $before; building $BRANCH at $SHA"

# --- Build -----------------------------------------------------------------------------------------
# No `npm audit fix --force`: the 8 reported vulnerabilities are known and that command breaks the build.
if [ "$EXEC" -eq 1 ]; then
  cd "$FRONT"
  say "+ npm ci"
  npm ci
  say "+ NODE_OPTIONS=--max-old-space-size=5120 npm run build   (expect 'built in ...'; the >500 kB chunk warning is normal)"
  NODE_OPTIONS=--max-old-space-size=5120 npm run build 2>&1 | tail -15
else
  say "would run: (in $FRONT) npm ci"
  say "would run: (in $FRONT) NODE_OPTIONS=--max-old-space-size=5120 npm run build"
fi

# --- Check the build BEFORE touching the live copy -------------------------------------------------
check_dist() {
  local dir="$1"
  [ -f "$dir/index.html" ] || { say "FAIL: $dir/index.html is missing"; return 1; }
  local n=0 missing=0 ref
  while read -r ref; do
    n=$((n+1))
    [ -f "$dir${ref}" ] || { say "FAIL: referenced asset missing: $ref"; missing=$((missing+1)); }
  done < <(grep -oE '(src|href)="/assets/[^"]+"' "$dir/index.html" | sed -E 's/^(src|href)="//; s/"$//')
  [ "$n" -gt 0 ] || { say "FAIL: index.html references no /assets files"; return 1; }
  [ "$missing" -eq 0 ] || return 1
  say "ok: $dir has index.html and its $n referenced assets"
}
if [ "$EXEC" -eq 1 ]; then check_dist "$BUILT" || die "the new build is incomplete; the live UI was NOT touched"; else say "would check: $BUILT/index.html and every asset it references"; fi

# --- Install: keep the old UI so a bad build can be undone in seconds -------------------------------
run cp -a "$BUILT" "$LIVE.new"
[ "$EXEC" -eq 1 ] && printf 'commit %s built %s\n' "$SHA" "$STAMP" > "$LIVE.new/.built-from"
if [ -d "$LIVE" ]; then run mv "$LIVE" "$LIVE.bak-$STAMP"; fi
run mv "$LIVE.new" "$LIVE"

# --- Restart (applies any pending patch too, same script the cron uses) ----------------------------
run bash "$SRC/scripts/pilot/update.sh" --restart

# --- Verify what is actually served ----------------------------------------------------------------
if [ "$EXEC" -eq 1 ]; then
  sleep 3
  html="$(curl -fsS "http://127.0.0.1:$PORT/")" || die "the server does not answer on 127.0.0.1:$PORT. ROLLBACK: mv $LIVE $LIVE.failed && mv $LIVE.bak-$STAMP $LIVE && bash $SRC/scripts/pilot/update.sh --restart"
  bad=0
  while read -r ref; do
    code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT${ref}")"
    say "$code $ref"
    [ "$code" = "200" ] || bad=1
  done < <(printf '%s' "$html" | grep -oE '(src|href)="/assets/[^"]+"' | sed -E 's/^(src|href)="//; s/"$//')
  [ "$bad" -eq 0 ] || die "an asset is not served. ROLLBACK: mv $LIVE $LIVE.failed && mv $LIVE.bak-$STAMP $LIVE && bash $SRC/scripts/pilot/update.sh --restart"
  say "DONE: UI from $BRANCH at $SHA is served. Previous UI kept at $LIVE.bak-$STAMP"
  say "Then reload the page on the phone (Safari may keep the old bundle for a moment)."
else
  say "would verify: GET / on 127.0.0.1:$PORT and every asset named in the served index.html answers 200"
  say "ROLLBACK (if ever needed): mv $LIVE $LIVE.failed && mv $LIVE.bak-<stamp> $LIVE && bash $SRC/scripts/pilot/update.sh --restart"
fi

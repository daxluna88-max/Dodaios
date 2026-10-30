#!/usr/bin/env bash
# Pull-based deploy + smoke check for the pilot VPS. Runs from cron every minute.
# When the pilot branch has a new commit: apply the patches, restart Jarvis, run
# the smoke checks and publish the report to the `pilot-results` branch, so the
# result can be read without anyone pasting terminal output.
set -uo pipefail
P="${PILOT_HOME:-$HOME/.pilot}"
REPO="${PILOT_REPO:-daxluna88-max/Dodaios}"
BRANCH="${PILOT_BRANCH:-claude/it-complete}"
LS_URL="${PILOT_LS_URL:-https://github.com/$REPO}"
PUSH_URL="${PILOT_PUSH_URL:-git@github.com:$REPO.git}"
[ -f "$P/env" ] && . "$P/env"
mkdir -p "$P/pending"
export GIT_SSH_COMMAND="ssh -i $P/deploy_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$P/known_hosts"
publish() { # publish <report-file> <sha>; non-zero when the push did not go through
  local pub; pub="$(mktemp -d)"
  if git ls-remote --exit-code "$PUSH_URL" refs/heads/pilot-results >/dev/null 2>&1; then
    git clone -q --depth 1 --branch pilot-results "$PUSH_URL" "$pub" || { rm -rf "$pub"; return 1; }
  else
    git -C "$pub" init -q -b pilot-results; git -C "$pub" remote add origin "$PUSH_URL"
  fi
  mkdir -p "$pub/reports"; cp "$1" "$pub/reports/$2.txt"; cp "$1" "$pub/reports/latest.txt"
  git -C "$pub" add -A
  git -C "$pub" -c user.name=pilot-vps -c user.email=pilot@localhost commit -q -m "report $2" \
    && git -C "$pub" push -q origin pilot-results
  local rc=$?; rm -rf "$pub"; return $rc
}
flush_pending() {
  local f
  for f in "$P"/pending/*.txt; do
    [ -e "$f" ] || continue
    publish "$f" "$(basename "$f" .txt)" && rm -f "$f"
  done
}
flush_pending

sha="$(git ls-remote "$LS_URL" "refs/heads/$BRANCH" | cut -f1)"
[ -n "$sha" ] || exit 0
last="$(cat "$P/last_sha" 2>/dev/null || true)"
if [ "$sha" = "$last" ] && [ ! -e "$P/force" ]; then exit 0; fi
rm -f "$P/force"

work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
RAW="${PILOT_RAW:-https://raw.githubusercontent.com/$REPO/$sha/scripts/pilot}"
report="$work/report.txt"
{
  echo "sha=$sha started=$(date -u +%FT%TZ)"
  curl -fsSL "$RAW/MANIFEST" -o "$work/MANIFEST" || echo "fetch MANIFEST failed"
  while read -r name _; do [ -n "$name" ] && { curl -fsSL "$RAW/$name" -o "$work/$name" || echo "fetch $name failed"; }; done < "$work/MANIFEST"
  for f in update.sh check.py; do curl -fsSL "$RAW/$f" -o "$work/$f" || echo "fetch $f failed"; done
  if [ -n "${PILOT_DRY:-}" ]; then echo "dry run: update and checks skipped"; else
    JARVIS_PILOT_BASE="file://$work" bash "$work/update.sh"; echo "update_exit=$?"
    "$HOME/.dodaios/.venv/bin/python" -I "$work/check.py"; echo "check_exit=$?"
  fi
  echo "finished=$(date -u +%FT%TZ)"
} > "$report" 2>&1
echo "$sha" > "$P/last_sha"
cp "$report" "$P/pending/$sha.txt"
flush_pending

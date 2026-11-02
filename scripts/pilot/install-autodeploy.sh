#!/usr/bin/env bash
# One-time setup of the pull-based deploy on the VPS (idempotent).
set -euo pipefail
command -v crontab >/dev/null || { echo "STOP: crontab not installed"; exit 1; }
command -v flock >/dev/null || { echo "STOP: flock not installed"; exit 1; }
P="$HOME/.pilot"; mkdir -p "$P"
echo "export PATH=\"$PATH\"" > "$P/env"
curl -fsSL "https://raw.githubusercontent.com/daxluna88-max/Dodaios/${1:-claude/it-complete}/scripts/pilot/autodeploy.sh" -o "$P/autodeploy.sh"
chmod +x "$P/autodeploy.sh"
[ -f "$P/deploy_key" ] || ssh-keygen -q -t ed25519 -N "" -C "pilot-vps" -f "$P/deploy_key"
( crontab -l 2>/dev/null | grep -v "pilot/autodeploy" || true
  # -o: the server a run restarts must not inherit (and hold forever) the lock; run.lock is a fresh file.
  echo "* * * * * flock -o -n $P/run.lock $P/autodeploy.sh >> $P/cron.log 2>&1" ) | crontab -
echo "Installato. Chiave pubblica da aggiungere su GitHub (Deploy keys, con scrittura):"
cat "$P/deploy_key.pub"

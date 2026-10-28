#!/bin/bash
# DEFINITION - NOT RUN. Root-only, idempotent creation of the per-customer layout on the
# mounted volume (/data). bash -n only. See docs/VOLUME-AND-UPDATES.md for the layout.
set -euo pipefail
umask 077
[ "$(id -u)" -eq 0 ] || { echo "must run as root" >&2; exit 77; }
DATA="${JARVIS_VOLUME:-/data}"
UID_J=2000
mountpoint -q "$DATA" || { echo "refusing: $DATA is not a mount point (would write to the root disk)" >&2; exit 78; }
# Marker per ADR-0054: JSON {"product":"consumer"}; never overwrite a foreign marker.
root="$DATA/jarvis-consumer"
install -d -m 0750 -o "$UID_J" -g "$UID_J" "$root"
if [ -e "$root/.product" ]; then
  grep -q '"product": *"consumer"' "$root/.product" || { echo "refusing: foreign .product marker" >&2; exit 3; }
else
  tmp="$(mktemp "$root/.product.XXXXXX")"
  printf '{"product": "consumer"}' > "$tmp"
  chown "$UID_J:$UID_J" "$tmp"; chmod 0600 "$tmp"
  mv -T "$tmp" "$root/.product"
fi
# TODO: also record customer_id in a root-owned $DATA/.customer (0444) and compare to the
# control-plane value at every boot (cross-mount refusal, ADR-0065 section 4).
for d in memory wiki skills logs outputs society; do
  install -d -m 0700 -o "$UID_J" -g "$UID_J" "$root/$d"
done
for d in home home/Downloads home/Documents home/.config home/.cache work cache; do
  install -d -m 0700 -o "$UID_J" -g "$UID_J" "$DATA/$d"
done
# Downloads quarantine: a separate dir the browser may write; the agent moves files out explicitly.
install -d -m 0700 -o "$UID_J" -g "$UID_J" "$DATA/home/Downloads"
# Root-owned, agent-readable pin of the release that last migrated this volume (rollback guard).
install -d -m 0755 -o root -g root "$DATA/.node"
echo "jarvis-data-init: layout ensured under $DATA (not executed in this design pass)"

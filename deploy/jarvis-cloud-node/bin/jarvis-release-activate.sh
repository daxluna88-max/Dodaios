#!/bin/bash
# DEFINITION - NOT RUN. Root supervisor script: verify, unpack and activate an immutable release
# (A/B). bash -n only. Usage: jarvis-release-activate.sh <bundle.tar> <bundle.tar.sig> [--rollback]
# No pip/uv/npm runs here or at runtime: the bundle already contains the venv, the prebuilt UI
# dist and the Playwright browsers. Signature scheme is an OWNER DECISION (minisign vs cosign vs gpg);
# the verifier below is a PLACEHOLDER hook, the script fails closed without it.
set -euo pipefail
umask 022
[ "$(id -u)" -eq 0 ] || { echo "must run as root" >&2; exit 77; }
REL=/opt/jarvis/releases
CUR=/opt/jarvis/current
PREV=/opt/jarvis/previous
VERIFY="${JARVIS_VERIFY_CMD:-/opt/jarvis-supervisor/verify-signature}"   # TODO(owner): write/choose

if [ "${1:-}" = "--rollback" ]; then
  [ -L "$PREV" ] || { echo "no previous release recorded" >&2; exit 1; }
  target="$(readlink -f "$PREV")"
  ln -sfn "$target" "$CUR.new" && mv -T "$CUR.new" "$CUR"
  systemctl restart jarvis-runtime.service
  echo "rolled back to $target"; exit 0
fi

bundle="${1:?bundle}"; sig="${2:?signature}"
[ -x "$VERIFY" ] || { echo "no signature verifier installed: refusing (fail closed)" >&2; exit 78; }
"$VERIFY" "$bundle" "$sig"

ver="$(tar -xOf "$bundle" ./RELEASE 2>/dev/null | head -n1 | tr -cd 'A-Za-z0-9._-')"
[ -n "$ver" ] || { echo "bundle has no RELEASE file" >&2; exit 65; }
dest="$REL/$ver"
[ ! -e "$dest" ] || { echo "release $ver already present" >&2; exit 73; }
stage="$(mktemp -d "$REL/.stage.XXXXXX")"
trap 'rm -rf "$stage"' EXIT
tar --no-same-owner --no-same-permissions -xf "$bundle" -C "$stage"
chown -R root:root "$stage"
chmod -R go-w "$stage"
# Pre-migration snapshot of the volume is the control plane's job BEFORE this script (ADR-0065 3.3).
mv -T "$stage" "$dest"; trap - EXIT
[ -L "$CUR" ] && ln -sfn "$(readlink -f "$CUR")" "$PREV"
ln -sfn "$dest" "$CUR.new" && mv -T "$CUR.new" "$CUR"
apparmor_parser -r /etc/apparmor.d/jarvis-chromium
systemctl restart jarvis-runtime.service
echo "activated $ver (previous kept for rollback)"

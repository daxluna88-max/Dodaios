#!/usr/bin/env bash
# Jarvis pilot diagnostic: reads state, changes nothing. One-screen PASS/WARN/FAIL.
# Never prints secret values: only variable NAMES (strict NAME= lines) and file modes.
# Writes no file unless --save is given (then ~/diag-last.txt, mode 600).
# --test runs a provider call that MAY COST MONEY: it needs --allow-spend as well.
set -u
umask 077
BASE="http://127.0.0.1:47821"
LOG="$HOME/jarvis-serve.log"
ENVF="$HOME/.dodaios.env"
OUT="$HOME/diag-last.txt"
SAVE=0; TEST=0; SPEND=0
for a in "$@"; do case "$a" in --save) SAVE=1;; --test) TEST=1;; --allow-spend) SPEND=1;; esac; done
redact_url() { sed -E 's#(://)[^/@]*@#\1#'; }
export PATH="$HOME/.local/bin:$PATH"

ok()   { printf 'PASS  %s\n' "$*"; }
warn() { printf 'WARN  %s\n' "$*"; }
bad()  { printf 'FAIL  %s\n' "$*"; }
get()  { curl -s -m 8 "$BASE$1" 2>/dev/null; }
jq_()  { python3 -c "import sys,json
try:
    d=json.load(sys.stdin)
except Exception:
    print('?'); sys.exit()
for k in sys.argv[1].split('.'):
    d=d.get(k) if isinstance(d,dict) else None
print('?' if d is None else d)" "$1"; }

run() {
  echo "== Jarvis diag $(date -u +%FT%TZ) on $(hostname) =="

  # 1. version actually running
  if [ -d "$HOME/.dodaios/.git" ]; then
    sha=$(git -C "$HOME/.dodaios" rev-parse --short HEAD 2>/dev/null)
    url=$(git -C "$HOME/.dodaios" remote get-url origin 2>/dev/null | redact_url)
    case "$url" in
      *daxluna88-max*) ok "checkout: $sha from fork ($url) [running process not correlated]" ;;
      *) warn "checkout: $sha from $url (NOT the fork) [running process not correlated]" ;;
    esac
  else
    warn "code: ~/.dodaios is not a git checkout"
  fi

  # 2. process and listening address
  pid=$(pgrep -x jarvis | head -1)
  if [ -z "$pid" ]; then bad "server: no jarvis process"; else
    ok "server: pid $pid"
    if ss -tln 2>/dev/null | grep -q '0\.0\.0\.0:47821\|\[::\]:47821'; then
      bad "server: port 47821 listens on ALL interfaces"
    elif ss -tln 2>/dev/null | grep -q '127\.0\.0\.1:47821'; then
      ok "server: 47821 on loopback only"
    else
      warn "server: 47821 not listening"
    fi
  fi

  # 3. health
  h=$(get /api/health)
  [ "$(printf %s "$h" | jq_ ok)" = "True" ] && ok "health: $(printf %s "$h" | jq_ version)" || bad "health: no valid answer"

  # 4. brain (configured != reachable; reachability is only proven by an explicit test)
  stat=$(get /api/brain/status)
  prim=$(printf %s "$stat" | jq_ provider)
  echo "INFO  brain.provider=$prim model=$(printf %s "$stat" | jq_ model)"
  case "$prim" in
    "?"|"") bad "brain: no provider answer" ;;
    codex|openai-codex) bad "brain: '$prim' is subagent-only and cannot be the main brain" ;;
    *) ok "brain: provider configured: $prim (reachability NOT checked)" ;;
  esac
  [ "$(printf %s "$stat" | jq_ model)" = "unknown" ] && warn "brain: model shown as unknown by the server"

  # 5. secrets file (names + permissions only)
  if [ -f "$ENVF" ]; then
    perm=$(stat -c %a "$ENVF")
    [ "$perm" = "600" ] && ok "secrets: $ENVF mode 600" || bad "secrets: $ENVF mode $perm (want 600)"
    echo "INFO  variable names: $(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ENVF" | cut -d= -f1 | tr '\n' ' ')"
  else
    warn "secrets: $ENVF missing"
  fi

  # 6. Codex login
  if command -v codex >/dev/null 2>&1; then
    ok "codex: $(timeout 8 codex --version 2>/dev/null | head -1)"
    cs=$(timeout 8 codex login status 2>&1 | head -1)
    case "$cs" in *"Logged in"*) ok "codex: login recognized";; *) warn "codex: login not recognized";; esac
  else
    warn "codex: not installed (npm config set prefix ~/.local; npm i -g @openai/codex)"
  fi

  # 7. agent sessions stored (the unfiltered list hides the society and side surfaces)
  cnt() { get "/api/agent-chat/sessions?limit=50&surface=$1" | python3 -c 'import sys,json
try: print(len(json.load(sys.stdin)["sessions"]))
except Exception: print("?")'; }
  echo "INFO  agent-chat sessions: jarvis=$(cnt jarvis) agent=$(cnt agent) society=$(cnt society)"

  # 8. current voice + telegram state (history counts are labelled as history)
  vr=$(get /api/voice/status | jq_ ready)
  echo "INFO  voice/status ready=$vr (False/disabled is expected with JARVIS_VOICE=0)"
  tg=$(get /api/config | python3 -c 'import sys,json
try:
    t=json.load(sys.stdin).get("integrations",{}).get("telegram",{})
    print("enabled=%s allowed_ids=%d" % (t.get("enabled"), len(t.get("allowed_user_ids",[]) or [])))
except Exception:
    print("unknown")')
  case "$tg" in "enabled=False"*) ok "telegram: $tg";; unknown) warn "telegram: state unknown";; *) warn "telegram: $tg";; esac
  if [ -f "$LOG" ]; then
    echo "INFO  log history (past events, not current state): voice-watchdog=$(grep -c 'Voice-ready watchdog' "$LOG") ws-disconnect=$(grep -c 'WebSocketDisconnect' "$LOG")"
  fi

  # 9. firewall (needs passwordless sudo; otherwise skipped, never prompts)
  if sudo -n true 2>/dev/null; then
    ufw=$(sudo -n ufw status 2>/dev/null | grep -E '^[0-9]' | awk '{print $1}' | sort -u | tr '\n' ' ')
    case "$ufw" in "22/tcp 22/tcp (v6) "|"22/tcp "|"22/tcp (v6) 22/tcp ") ok "ufw: only 22/tcp";; *) warn "ufw rules: $ufw";; esac
  else
    echo "INFO  ufw: skipped (sudo needs password)"
  fi

  # 10. resources
  echo "INFO  $(free -h | awk '/Mem:/{print "RAM used "$3" of "$2}'), disk $(df -h / | awk 'NR==2{print $5" used"}')"

  # 11. optional provider call: may spend money
  if [ "$TEST" = 1 ]; then
    if [ "$SPEND" = 1 ] && [ -n "$prim" ] && [ "$prim" != "?" ]; then
      echo "INFO  provider test ($prim, one minimal call): $(timeout 30 jarvis brain test "$prim" 2>/dev/null | python3 -c 'import sys,json
try:
    d=json.load(sys.stdin); print(d.get("status"), d.get("latency_ms"))
except Exception:
    print("no parsable answer")')"
    else
      echo "INFO  provider test skipped: it may cost money; add --allow-spend to run it"
    fi
  fi
  echo "== end =="
}

if [ "$SAVE" = 1 ]; then run "$@" | tee "$OUT"; chmod 600 "$OUT"; else run "$@"; fi

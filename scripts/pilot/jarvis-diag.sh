#!/usr/bin/env bash
# Jarvis pilot diagnostic: READ-ONLY. Prints one-screen PASS/WARN/FAIL summary.
# Never prints secret values (only variable NAMES and file permissions).
# Optional: --test also runs `jarvis brain test <primary>` (one tiny provider call).
set -u
BASE="http://127.0.0.1:47821"
LOG="$HOME/jarvis-serve.log"
ENVF="$HOME/.dodaios.env"
OUT="$HOME/diag-last.txt"
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
    url=$(git -C "$HOME/.dodaios" remote get-url origin 2>/dev/null)
    case "$url" in
      *daxluna88-max*) ok "code: $sha from fork ($url)" ;;
      *) warn "code: $sha from $url (NOT the fork)" ;;
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

  # 4. brain
  prim=$(get /api/config | jq_ brain.primary)
  stat=$(get /api/brain/status)
  echo "INFO  brain.primary=$prim status.provider=$(printf %s "$stat" | jq_ provider) model=$(printf %s "$stat" | jq_ model)"
  case "$prim" in openrouter|gemini|openai|grok|claude-api|vertex) ok "brain: main provider set";; *) bad "brain: main provider '$prim' (codex/ollama/claude-api without key will not answer)";; esac
  [ "$(printf %s "$stat" | jq_ model)" = "unknown" ] && warn "brain: model unknown (pin one with set-brain-model)"

  # 5. secrets file (names + permissions only)
  if [ -f "$ENVF" ]; then
    perm=$(stat -c %a "$ENVF")
    [ "$perm" = "600" ] && ok "secrets: $ENVF mode 600" || bad "secrets: $ENVF mode $perm (want 600)"
    echo "INFO  variable names: $(cut -d= -f1 "$ENVF" | tr '\n' ' ')"
  else
    warn "secrets: $ENVF missing"
  fi

  # 6. Codex login
  if command -v codex >/dev/null 2>&1; then
    ok "codex: $(codex --version 2>&1 | head -1)"
    cs=$(codex login status 2>&1 | head -1)
    case "$cs" in *"Logged in"*) ok "codex: $cs";; *) warn "codex: $cs";; esac
  else
    warn "codex: not installed (npm config set prefix ~/.local; npm i -g @openai/codex)"
  fi

  # 7. agent sessions stored
  n=$(get "/api/agent-chat/sessions?limit=50" | python3 -c 'import sys,json
try: print(len(json.load(sys.stdin)["sessions"]))
except Exception: print("?")')
  echo "INFO  agent-chat sessions stored: $n"

  # 8. log findings (counts only)
  if [ -f "$LOG" ]; then
    echo "INFO  log: voice-watchdog=$(grep -c 'Voice-ready watchdog' "$LOG") telegram=$(grep -c 'Telegram-Channel' "$LOG") ws-disconnect=$(grep -c 'WebSocketDisconnect' "$LOG")"
    [ "$(grep -c 'Voice-ready watchdog' "$LOG")" -gt 0 ] && warn "voice: pipeline never ready (UI keeps 'Voice starting')"
    [ "$(grep -c 'Telegram-Channel' "$LOG")" -gt 0 ] && warn "telegram: channel active though not configured by us"
  else
    warn "log: $LOG missing"
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

  # 11. optional provider call
  if [ "${1:-}" = "--test" ] && [ -n "$prim" ] && [ "$prim" != "?" ]; then
    echo "INFO  provider test: $(jarvis brain test "$prim" 2>&1 | tr -d '\n ' | cut -c1-160)"
  fi
  echo "== end =="
}

run "$@" | tee "$OUT"

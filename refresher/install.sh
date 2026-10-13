#!/usr/bin/env bash
# Job Buddy Refresher: one-click installer for Linux. Run it with:  bash refresher/install.sh
# It sets everything up, connects to the Job Buddy website, and makes the refresher start with the computer.
# Running it again is safe: it updates the program and keeps the connection and the data.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOME_DIR="${JBR_HOME:-$HOME/job-buddy-refresher}"
SERVICE="job-buddy-refresher"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
fail() { printf '\n\033[31mStopped: %s\033[0m\n' "$*" >&2; exit 1; }

say "Job Buddy Refresher: installing into $HOME_DIR"
[ "$(uname -s)" = "Linux" ] || fail "this installer is for Linux."

# 1. Python
command -v python3 >/dev/null 2>&1 || fail "Python 3 isn't installed. Run:  sudo apt install python3 python3-venv python3-pip   then run this installer again."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || fail "Python 3.10 or newer is needed (this is $(python3 -V 2>&1)). On Ubuntu 22.04 or newer it is already included."
python3 -c 'import venv, ensurepip' >/dev/null 2>&1 \
  || fail "Python's venv part is missing. Run:  sudo apt install python3-venv python3-pip   then run this installer again."
ok "Python $(python3 -V 2>&1 | cut -d' ' -f2) is fine"

# 2. Copy the program
for item in app refresher requirements.txt requirements-ingest.txt; do
  [ -e "$SRC/$item" ] || fail "'$item' is missing next to this installer. Download the whole Job Buddy ZIP again and unzip it fully."
done
mkdir -p "$HOME_DIR/data" "$HOME_DIR/logs"
if [ "$(cd "$SRC" && pwd -P)" != "$(cd "$HOME_DIR" && pwd -P)" ]; then
  rm -rf "$HOME_DIR/app" "$HOME_DIR/refresher"
  cp -r "$SRC/app" "$SRC/refresher" "$HOME_DIR/"
  cp "$SRC/requirements.txt" "$SRC/requirements-ingest.txt" "$HOME_DIR/"
  find "$HOME_DIR/app" "$HOME_DIR/refresher" -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
fi
cp "$HOME_DIR/refresher/uninstall.sh" "$HOME_DIR/uninstall.sh"; chmod +x "$HOME_DIR/uninstall.sh"
ok "Program files are in place"

# 3. The tools it needs (this takes a minute or two the first time)
[ -x "$HOME_DIR/venv/bin/python" ] || python3 -m venv "$HOME_DIR/venv"
if [ -z "${JBR_SKIP_PIP:-}" ]; then
  say "Installing the job-fetching tools (a minute or two)..."
  "$HOME_DIR/venv/bin/pip" install --quiet --upgrade pip
  "$HOME_DIR/venv/bin/pip" install --quiet -r "$HOME_DIR/requirements-ingest.txt" \
    || fail "couldn't download the tools. Check the computer's internet connection and run this installer again."
  ok "Tools installed"
fi

# 4. Connect to Job Buddy
cd "$HOME_DIR"
CODE="${1:-${JBR_CODE:-}}"
if [ -z "$CODE" ] && [ -f "$HOME_DIR/refresher.json" ]; then
  ok "Already connected to Job Buddy (keeping the existing connection)"
else
  if [ -z "$CODE" ]; then
    say "Paste the connection code from Job Buddy (Admin > Refresher) and press Enter:"
    read -r CODE
  fi
  "$HOME_DIR/venv/bin/python" -m refresher --home "$HOME_DIR" pair "$CODE" \
    || fail "couldn't connect with that code. Ask Chandan for a fresh one and run this installer again."
fi

# 5. The helper command (jbr)
cat > "$HOME_DIR/jbr" <<'JBR'
#!/usr/bin/env bash
# jbr: look after the Job Buddy Refresher.   jbr status | logs | refresh | restart | stop | start | uninstall
HOME_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
SERVICE="job-buddy-refresher"
py() { (cd "$HOME_DIR" && "$HOME_DIR/venv/bin/python" -m refresher --home "$HOME_DIR" "$@"); }
case "${1:-status}" in
  status)    systemctl --user --no-pager status "$SERVICE" 2>/dev/null | head -n 8 || echo "(not running as a service)"; echo; py status ;;
  logs)      tail -n 100 -f "$HOME_DIR/logs/refresher.log" ;;
  refresh)   py once ;;
  restart)   systemctl --user restart "$SERVICE" && echo "Restarted." ;;
  stop)      systemctl --user stop "$SERVICE" && echo "Stopped. It will start again after a restart, or run: jbr start" ;;
  start)     systemctl --user start "$SERVICE" && echo "Started." ;;
  uninstall) exec "$HOME_DIR/uninstall.sh" ;;
  *) echo "Usage: jbr status | logs | refresh | restart | stop | start | uninstall"; exit 1 ;;
esac
JBR
chmod +x "$HOME_DIR/jbr"
mkdir -p "$HOME/.local/bin" && ln -sf "$HOME_DIR/jbr" "$HOME/.local/bin/jbr"
ok "Added the 'jbr' command"

# 6. Start with the computer
if [ -z "${JBR_SKIP_SERVICE:-}" ]; then
  command -v systemctl >/dev/null 2>&1 || fail "this Linux doesn't use systemd, so it can't be set to start automatically. Ask Chandan."
  UNIT_DIR="$HOME/.config/systemd/user"
  mkdir -p "$UNIT_DIR"
  cat > "$UNIT_DIR/$SERVICE.service" <<UNIT
[Unit]
Description=Job Buddy Refresher
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$HOME_DIR
Environment=PYTHONUNBUFFERED=1
Environment=JBR_HOME=$HOME_DIR
ExecStart=$HOME_DIR/venv/bin/python -m refresher --home $HOME_DIR run
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload
  systemctl --user enable "$SERVICE" >/dev/null 2>&1
  systemctl --user restart "$SERVICE"
  ok "It is running, and set to start whenever the computer starts"
  if ! loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q "Linger=yes"; then
    say "One last step: so it keeps running after a restart with nobody logged in, Linux needs your password once."
    sudo loginctl enable-linger "$USER" && ok "Done" \
      || warn "That didn't work. The refresher still runs, but only while you are logged in. Ask Chandan."
  fi
fi

say "All done."
echo "  • Look at how it is doing:   jbr status        (open a new Terminal window first if 'jbr' isn't found)"
echo "  • Watch what it is doing:    jbr logs"
echo "  • It also shows on the Job Buddy website: Admin > Refresher."
echo

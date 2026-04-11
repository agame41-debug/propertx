#!/bin/bash
# Rentero — spuštění webového rozhraní (macOS)
# Otevře nové okno Terminálu s živými logy serveru.

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT=8000
URL="http://localhost:$PORT"

mkdir -p "$PROJECT_DIR/cache"

# Pokud jsme uvnitř serverového okna Terminálu, spustíme server přímo
if [ "${RENTERO_SERVER_WINDOW:-}" = "1" ]; then
  export PYTHONUTF8=1
  export PYTHONIOENCODING=utf-8

  if [ -f "$PROJECT_DIR/.env" ]; then
    set -a; . "$PROJECT_DIR/.env"; set +a
  fi

  if [ -z "${RENTERO_SESSION_SECRET:-}" ] || [ -z "${RENTERO_USERNAME:-}" ] || [ -z "${RENTERO_PASSWORD:-}" ]; then
    export RENTERO_ALLOW_INSECURE_DEFAULTS=1
  fi

  PYTHON="$PROJECT_DIR/.venv/bin/python"
  if [ ! -f "$PYTHON" ]; then
    for cmd in python3.14 python3.13 python3.12 python3.11 python3 python; do
      if command -v "$cmd" &>/dev/null; then PYTHON="$cmd"; break; fi
    done
  fi

  echo "==============================="
  echo "  Rentero — $URL"
  echo "  Python: $PYTHON"
  echo "  Ctrl+C pro zastavení"
  echo "==============================="
  echo ""
  cd "$PROJECT_DIR"
  exec "$PYTHON" -u run_web.py --port "$PORT"
fi

# --- Launcher část (spouští se při dvojkliku nebo z externího terminálu) ---

# Zastavit předchozí instanci
EXISTING=$(lsof -ti tcp:$PORT 2>/dev/null)
if [ -n "$EXISTING" ]; then
  echo "Zastavuji předchozí instanci (PID $EXISTING)..."
  kill "$EXISTING" 2>/dev/null
  sleep 0.5
fi

# Otevřít nové okno Terminálu se serverem
SCRIPT_PATH="$PROJECT_DIR/start_web.sh"
osascript <<EOF
tell application "Terminal"
  activate
  set newTab to do script "RENTERO_SERVER_WINDOW=1 bash '$SCRIPT_PATH'"
  set custom title of front window to "Rentero Server"
end tell
EOF

# Počkat až server nastartuje (max 10 sekund)
echo "Čekám na start serveru..."
for i in $(seq 1 20); do
  if curl -s -o /dev/null -w "%{http_code}" "$URL/login" 2>/dev/null | grep -q "200"; then
    echo "Rentero běží na $URL"
    open "$URL"
    exit 0
  fi
  sleep 0.5
done

echo "Server se nespustil do 10 sekund — zkontrolujte okno Terminálu."
exit 1

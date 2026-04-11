#!/bin/bash
# Деплой Propertx на продакшн (Hetzner)
# Использование: ./deploy.sh
set -euo pipefail

SERVER="rentero@204.168.216.181"
REMOTE_DIR="~/rentero"
SERVICE_NAME="rentero"

echo "=== Deploying Propertx ==="

# 1. Проверяем ветку и статус
BRANCH=$(git branch --show-current)
echo "[1/3] Ветка: $BRANCH"

if [ -n "$(git status --porcelain)" ]; then
  echo "  ВНИМАНИЕ: Есть незакоммиченные изменения."
  read -p "  Закоммитить и продолжить? [y/N] " -n 1 -r
  echo
  if [[ $REPLY =~ ^[Yy]$ ]]; then
    git add -A
    git commit -m "deploy: $(date +%Y-%m-%d_%H:%M)"
  else
    echo "  Отменено."
    exit 1
  fi
fi

# 2. Push в GitHub
echo "[2/3] Push в GitHub..."
git push origin "$BRANCH"

# 3. Обновляем сервер и перезапускаем
echo "[3/3] Обновляю сервер..."
ssh "$SERVER" bash -s <<'DEPLOY'
  set -euo pipefail
  cd ~/rentero

  git pull origin $(git branch --show-current)
  source venv/bin/activate
  pip install -r requirements.txt --quiet --upgrade

  sudo systemctl restart rentero
  sleep 2

  if systemctl is-active --quiet rentero; then
    echo "  Rentero запущен!"
  else
    echo "  ОШИБКА: Rentero не запустился!"
    journalctl -u rentero --no-pager -n 20
    exit 1
  fi
DEPLOY

echo ""
echo "=== Деплой завершён ==="
echo "  https://propertx.eu"

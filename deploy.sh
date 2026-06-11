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

# Никакого автокоммита: рабочее дерево часто содержит чужой/живой WIP,
# и `git add -A` отправил бы в прод нерассмотренную смесь правок.
if [ -n "$(git status --porcelain)" ]; then
  echo "  ОШИБКА: Есть незакоммиченные изменения:"
  git status --short | sed 's/^/    /'
  echo "  Закоммить нужные hunky вручную (git add -p) и запусти деплой снова."
  exit 1
fi

if [ "$BRANCH" != "main" ]; then
  read -p "  ВНИМАНИЕ: деплой ветки '$BRANCH' (не main). Продолжить? [y/N] " -n 1 -r
  echo
  [[ $REPLY =~ ^[Yy]$ ]] || { echo "  Отменено."; exit 1; }
fi

# 2. Push в GitHub
echo "[2/3] Push в GitHub..."
git push origin "$BRANCH"

# 3. Обновляем сервер и перезапускаем — checkout ИМЕННО задеплоенной ветки,
#    а не той, на которой стоит серверный clone.
echo "[3/3] Обновляю сервер..."
ssh "$SERVER" "bash -s -- '$BRANCH'" <<'DEPLOY'
  set -euo pipefail
  BRANCH="$1"
  cd ~/rentero

  git fetch origin "$BRANCH"
  git checkout "$BRANCH"
  git pull --ff-only origin "$BRANCH"
  source venv/bin/activate
  # Без --upgrade: зависимости не запинены, апгрейд при каждом деплое
  # молча подтягивал бы новые мажоры FastAPI/Starlette/uvicorn.
  pip install -r requirements.txt --quiet

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

#!/bin/bash
# Деплой Rentero на продакшн
SERVER="rentero@204.168.216.181"

echo "=== Deploying Rentero ==="

# 1. Пуш в GitHub
git add .
git commit -m "update" 2>/dev/null
git push origin main

# 2. Обновить сервер
ssh $SERVER bash -c "'
  cd ~/rentero
  git pull origin main
  source venv/bin/activate
  pip install -r requirements.txt --quiet
  sudo systemctl restart rentero
  sleep 2
  systemctl is-active rentero
'"

echo "=== Done ==="

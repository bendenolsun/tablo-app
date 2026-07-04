#!/bin/bash
# Deploy öncesi sunucudaki güncel şablonları data_default'a kaydet, sonra deploy et
set -e

COOKIE="/tmp/tablo_deploy_cookies.txt"
BASE="https://www.thesalefy.com"
DATA_DEFAULT="data_default/templates.json"

echo "→ Sunucuya giriş yapılıyor..."
curl -s -c "$COOKIE" -X POST "$BASE/admin/login" -d "password=ifep.2024" -L -o /dev/null

echo "→ Güncel şablonlar çekiliyor..."
EXPORTED=$(curl -s -b "$COOKIE" "$BASE/admin/export-templates")

if [ -z "$EXPORTED" ] || [ "$EXPORTED" = "null" ]; then
  echo "⚠ Şablonlar çekilemedi, mevcut data_default korunuyor"
else
  COUNT=$(echo "$EXPORTED" | python3 -c "import json,sys; t=json.load(sys.stdin); print(len(t))" 2>/dev/null)
  echo "→ $COUNT şablon bulundu, data_default güncelleniyor..."
  echo "$EXPORTED" > "$DATA_DEFAULT"
  git add "$DATA_DEFAULT"
  git diff --cached --quiet || git commit -m "backup: deploy öncesi şablonlar senkronize edildi ($COUNT şablon)"
fi

echo "→ Railway deploy başlatılıyor..."
~/.railway/bin/railway up --detach
echo "✓ Deploy gönderildi"

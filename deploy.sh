#!/bin/bash
# Deploy öncesi sunucudaki güncel verileri data_default'a kaydet, sonra deploy et
set -e

BASE="https://www.thesalefy.com"
TOKEN="ifep.2024"
CHANGED=0

sync_json() {
  local ENDPOINT="$1" DEST="$2" LABEL="$3"
  echo "→ $LABEL çekiliyor..."
  DATA=$(curl -s "$BASE/$ENDPOINT?token=$TOKEN")
  if [ -z "$DATA" ] || [ "$DATA" = "null" ] || [ "$DATA" = "Unauthorized" ]; then
    echo "  ⚠ $LABEL çekilemedi, mevcut dosya korunuyor"
    return
  fi
  COUNT=$(echo "$DATA" | python3 -c "import json,sys; t=json.load(sys.stdin); print(len(t))" 2>/dev/null || echo "?")
  echo "  → $COUNT kayıt bulundu"
  echo "$DATA" > "$DEST"
  git add "$DEST"
  CHANGED=1
}

sync_json "admin/export-templates" "data_default/templates.json" "Şablonlar"
sync_json "admin/export-orders"    "data_default/orders.json"    "Siparişler"

if [ "$CHANGED" = "1" ]; then
  git diff --cached --quiet || git commit -m "backup: deploy öncesi veriler senkronize edildi"
fi

echo "→ Railway deploy başlatılıyor..."
~/.railway/bin/railway up --detach
echo "✓ Deploy gönderildi"

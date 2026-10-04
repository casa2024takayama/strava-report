#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Strava データ取得 → HTML レポート生成 → ローカルサーバーで開く
# 使い方:  bash update_report.sh
# ─────────────────────────────────────────────────────────────────

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

VENV="$DIR/.venv"
PYTHON="$VENV/bin/python3"
if [ ! -x "$PYTHON" ]; then
  echo "▶ 初回セットアップ: .venv を作成しています…"
  python3 -m venv "$VENV"
  "$PYTHON" -m pip install -q -r requirements.txt
  echo "✓ .venv 準備完了"
fi

echo "═══════════════════════════════════════"
echo "  🏃 Strava レポート更新"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "═══════════════════════════════════════"

echo ""
echo "▶ Step 1: Strava データ取得（前月＋当月）..."
PREV=$(date -v-1m +%Y-%m 2>/dev/null || python3 -c "from datetime import date; t=date.today(); print(f'{t.year}-12' if t.month==1 else f'{t.year}-{t.month-1:02d}')")
for YM in "$PREV" "$(date +%Y-%m)"; do
  echo "  → $YM"
  TARGET_YEAR_MONTH="$YM" "$PYTHON" strava_fetch.py || exit 1
done

echo ""
echo "▶ Step 2: AI コーチング（Claude）..."
# AI 評価は既定で停止中（API クレジット切れのため）。.env に COACH_ENABLED=1 で再開。
if [ "${COACH_ENABLED:-0}" != "1" ] && ! grep -qE '^COACH_ENABLED=1' .env 2>/dev/null; then
  echo "⏸  AI 評価は停止中 — スキップ"
elif [ -n "${ANTHROPIC_API_KEY:-}" ] || grep -qE '^ANTHROPIC_API_KEY=.' .env 2>/dev/null; then
  for YM in "$PREV" "$(date +%Y-%m)"; do
    echo "  → $YM"
    "$PYTHON" coach_claude.py --month "$YM" || echo "⚠️  $YM のコーチングに失敗（続行）"
  done
else
  echo "⚠️  ANTHROPIC_API_KEY 未設定 — コーチングをスキップ"
fi

echo ""
echo "▶ Step 2.5: シューズ比較ページ生成..."
"$PYTHON" shoe_compare.py || echo "⚠️  シューズ比較の生成に失敗（続行）"

echo ""
echo "▶ Step 3: HTML レポート生成（ローカル版）..."
export REPORT_EDITION=local
for YM in "$PREV" "$(date +%Y-%m)"; do
  echo "  → $YM"
  TARGET_YEAR_MONTH="$YM" "$PYTHON" report_html.py || exit 1
done

echo ""
echo "▶ Step 4: ローカルサーバー起動（HTML 上の「データ更新」ボタンが使えます）"
echo "   → http://127.0.0.1:8766/index.html"
exec "$PYTHON" serve_report.py --open

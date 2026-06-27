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
echo "▶ Step 1: Strava データ取得..."
"$PYTHON" strava_fetch.py
if [ $? -ne 0 ]; then
  echo "❌ データ取得に失敗しました"
  exit 1
fi

echo ""
echo "▶ Step 2: AI コーチング（Claude）..."
if [ -n "${ANTHROPIC_API_KEY:-}" ] || grep -qE '^ANTHROPIC_API_KEY=.' .env 2>/dev/null; then
  "$PYTHON" coach_claude.py
  if [ $? -ne 0 ]; then
    echo "⚠️  AI コーチングに失敗しました（レポートは続行）"
  fi
else
  echo "⚠️  ANTHROPIC_API_KEY 未設定 — コーチングをスキップ"
fi

echo ""
echo "▶ Step 3: HTML レポート生成（ローカル版）..."
export REPORT_EDITION=local
"$PYTHON" report_html.py
if [ $? -ne 0 ]; then
  echo "❌ レポート生成に失敗しました"
  exit 1
fi

echo ""
echo "▶ Step 4: ローカルサーバー起動（HTML 上の「データ更新」ボタンが使えます）"
echo "   → http://127.0.0.1:8766/index.html"
exec "$PYTHON" serve_report.py --open

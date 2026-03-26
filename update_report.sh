#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Strava データ取得 → HTML レポート生成 → ブラウザで開く
# 使い方:  bash update_report.sh
# ─────────────────────────────────────────────────────────────────

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "═══════════════════════════════════════"
echo "  🏃 Strava レポート更新"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "═══════════════════════════════════════"

# 1. データ取得（キャッシュ済みはスキップ、新着だけ取得）
echo ""
echo "▶ Step 1: Strava データ取得..."
python3 strava_fetch.py
if [ $? -ne 0 ]; then
  echo "❌ データ取得に失敗しました"
  exit 1
fi

# 2. HTML レポート生成
echo ""
echo "▶ Step 2: HTML レポート生成..."
python3 report_html.py
if [ $? -ne 0 ]; then
  echo "❌ レポート生成に失敗しました"
  exit 1
fi

# 3. ブラウザで開く
echo ""
echo "▶ Step 3: ブラウザで開く..."
open "$DIR/report_march2026.html"

# 4. GitHub Pages へデプロイ
echo ""
echo "▶ Step 4: GitHub Pages へデプロイ..."
cp "$DIR/report_march2026.html" "$DIR/index.html"
git -C "$DIR" add index.html
git -C "$DIR" commit -m "Update report $(date '+%Y-%m-%d %H:%M')" 2>&1
git -C "$DIR" push origin main 2>&1
if [ $? -eq 0 ]; then
  echo "🌐 デプロイ完了！"
  echo "   → https://casa2024takayama.github.io/strava-report/"
else
  echo "⚠️  push に失敗しました（ローカルのレポートは更新済み）"
fi

echo ""
echo "✅ 完了！"

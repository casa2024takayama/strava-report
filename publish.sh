#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# 公開（オンライン版）: ローカルで生成して GitHub Pages へ push する。
#   - AI コーチングは Claude（Sonnet）で生成し、Garmin の回復・負荷も反映
#   - 生の日次健康データ（garmin_daily.csv）は公開しない（コミット対象外）
# 使い方:  bash publish.sh
# ※ 日次レポートの公開はこのスクリプトが担当。GitHub Actions の cron は停止済み。
# ─────────────────────────────────────────────────────────────────
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

PYTHON="$DIR/.venv/bin/python3"
if [ ! -x "$PYTHON" ]; then
  python3 -m venv "$DIR/.venv"
  "$PYTHON" -m pip install -q -r requirements.txt
fi

echo "▶ Step 1: Strava データ取得"
"$PYTHON" strava_fetch.py

echo "▶ Step 2: AI コーチング（Claude / Sonnet・Garmin 反映）"
if [ -n "${ANTHROPIC_API_KEY:-}" ] || grep -qE '^ANTHROPIC_API_KEY=.' .env 2>/dev/null; then
  "$PYTHON" coach_claude.py || echo "⚠️ コーチング失敗（レポートは続行）"
else
  echo "⚠️ ANTHROPIC_API_KEY 未設定 — コーチングをスキップ"
fi

echo "▶ Step 3: オンライン版 HTML 生成"
REPORT_EDITION=online "$PYTHON" report_html.py

echo "▶ Step 4: 公開（push）"
# ※ garmin_daily.csv（生の日次健康データ）は意図的に add しない＝非公開
# coaching_report_*.md / coach_cache_*.json は公開しない（Garmin月次集計を含むため）。
# 講評文は index.html に焼き込み済みなので、公開はHTMLとPBデータのみで十分。
git add index.html 20*.html pbs.json races.json
if git diff --staged --quiet; then
  echo "（変更なし — push スキップ）"
else
  git commit -m "Update report (Garmin/Claude) $(date '+%Y-%m-%d %H:%M JST')"
  git pull --rebase -X theirs origin main
  git push
  echo "🌐 公開完了 → https://casa2024takayama.github.io/strava-report/"
fi

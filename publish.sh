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

# ── 月初バックフィル判定 ─────────────────────────────────────────
# strava_fetch.py は当月1日〜末日しか取得しないため、月末最終日の朝の取得より
# 後に走ったラン（例: 7/31 夕方）は、翌月の実行では対象月が変わって永久に
# 取り込まれない。月初1〜3日は前月ぶんも再取得・再生成してこの穴を塞ぐ。
PREV_YM=""
if [ "$(date +%-d)" -le 3 ]; then
  # macOS(BSD date)は -v-1m、Linux(GNU date)は -d 'last month'
  PREV_YM="$(date -v-1m '+%Y-%m' 2>/dev/null || date -d 'last month' '+%Y-%m')"
fi
if [ -n "$PREV_YM" ]; then
  # ※ ${} 必須: 直後が全角文字だと bash 3.2 (macOS) が変数名を誤解釈し set -u で落ちる
  echo "▶ Step 1.5: 月初につき前月（${PREV_YM}）を再取得（月末ラン取りこぼし対策）"
  TARGET_YEAR_MONTH="$PREV_YM" "$PYTHON" strava_fetch.py \
    || { echo "⚠️ 前月の再取得に失敗（前月アーカイブは更新せず続行）"; PREV_YM=""; }
fi

echo "▶ Step 2: AI コーチング（Claude / Sonnet・Garmin 反映）"
if [ -n "${ANTHROPIC_API_KEY:-}" ] || grep -qE '^ANTHROPIC_API_KEY=.' .env 2>/dev/null; then
  "$PYTHON" coach_claude.py || echo "⚠️ コーチング失敗（レポートは続行）"
else
  echo "⚠️ ANTHROPIC_API_KEY 未設定 — コーチングをスキップ"
fi

echo "▶ Step 3: オンライン版 HTML 生成"
# 「ローカル版」リンクをスマホから開けるよう、Tailscale IP を自動検出して埋め込む
# （未設定時のみ。REPORT_LOCAL_URL を明示指定していればそれを優先）。
if [ -z "${REPORT_LOCAL_URL:-}" ] && command -v tailscale >/dev/null 2>&1; then
  TSIP="$(tailscale ip -4 2>/dev/null | head -1 | tr -d '[:space:]')"
  [ -n "$TSIP" ] && export REPORT_LOCAL_URL="http://$TSIP:8766/index.html" \
    && echo "  ローカル版リンク: $REPORT_LOCAL_URL"
fi
# 前月→当月の順で焼く（report_html.py はアーカイブと index.html の両方を書くため、
# 前月を先に焼き、最後の当月実行で index.html を正しい月に戻す）。
if [ -n "$PREV_YM" ]; then
  echo "  前月アーカイブ（${PREV_YM}）を焼き直し"
  REPORT_EDITION=online TARGET_YEAR_MONTH="$PREV_YM" "$PYTHON" report_html.py
fi
REPORT_EDITION=online "$PYTHON" report_html.py

push_with_retry() {
  local attempt=1 max=5
  while true; do
    git pull --rebase -X theirs origin main && git push && return 0
    git rebase --abort >/dev/null 2>&1 || true
    if [ "$attempt" -ge "$max" ]; then
      echo "❌ push failed after $attempt attempts" >&2
      return 1
    fi
    echo "⚠️ push 競合、再試行 ($attempt/$max)…" >&2
    sleep $(( (RANDOM % 4) + 2 ))
    attempt=$((attempt+1))
  done
}

echo "▶ Step 4: 公開（push）"
# ※ garmin_daily.csv（生の日次健康データ）は意図的に add しない＝非公開
# coaching_report_*.md / coach_cache_*.json は公開しない（Garmin月次集計を含むため）。
# 講評文は index.html に焼き込み済みなので、公開はHTMLとPBデータのみで十分。

# ローカル版のみに埋め込まれる REPORT_SERVER_TOKEN が、online実行で再生成されない
# 過去月アーカイブHTMLに残ったまま公開されるのを防ぐ（トークン行のみ除去）。
for f in index.html 20*.html; do
  [ -f "$f" ] || continue
  if grep -q 'const token = "[^"]' "$f"; then
    echo "⚠️ $f にローカル用トークンが混入 — 除去します"
    sed -i.bak 's/const token = "[^"]*";/const token = "";/' "$f" && rm -f "$f.bak"
  fi
done
if grep -l 'const token = "[^"]' index.html 20*.html 2>/dev/null; then
  echo "❌ トークンの除去に失敗 — 公開を中止します" >&2
  exit 1
fi

git add index.html 20*.html pbs.json races.json publish_meta.json
git add plan_*.json 2>/dev/null || true   # まだ存在しない場合は無視（set -e 対策）
if git diff --staged --quiet; then
  echo "（変更なし — push スキップ）"
else
  git commit -m "Update report (Garmin/Claude) $(date '+%Y-%m-%d %H:%M JST')"
  push_with_retry
  echo "🌐 公開完了 → https://casa2024takayama.github.io/strava-report/"
fi

echo "▶ Step 5: ローカル閲覧用に local 版へ焼き直し"
# Step 3 で index.html を online 版（Garmin ボタン等 local 限定 UI なし）に上書きするが、
# 常駐サーバー serve_report.py は起動時しか焼き直さない（既に起動中なら早期 return）。
# そのため夜間公開後は Garmin ボタンが消えた online 版をサーバーが配信し続け、
# スマホでは「ローカル版」バナーだけ出てボタンが無い状態になる。push 済みの online 版は
# 既にコミット済みなので、ディスク上の index.html／当月アーカイブだけ local 版へ戻す
# （コミットはしない。次回実行時は Step 3 が再び online 版で上書きする）。
# 月初は前月アーカイブも local 版へ（ローカルサーバーが配信する 20YYMM.html の整合）
if [ -n "$PREV_YM" ]; then
  REPORT_EDITION=local TARGET_YEAR_MONTH="$PREV_YM" "$PYTHON" report_html.py >/dev/null \
    || echo "⚠️ 前月ローカル版の焼き直し失敗（表示のみ）"
fi
REPORT_EDITION=local "$PYTHON" report_html.py >/dev/null \
  && echo "  ローカル版へ焼き直し完了（サーバーが Garmin ボタン付きを配信）" \
  || echo "⚠️ ローカル版の焼き直し失敗（表示のみ・公開には影響なし）"

#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# refresh.sh — 「最新に更新して表示」を1コマンドで安全に行う
#
#   1) 現在ブランチをリモート最新へ確実に同期（upstream 未設定でも動く）
#   2) 動いている古いサーバーを確実に停止（launchd / ポート 8766）
#   3) サーバーを起動（起動時に新コードで HTML を焼き直す）
#
# 「更新したのに反映されない」（ブランチ違い／古いサーバー居座り／
# ブラウザキャッシュ）を根本回避する。詳細は docs/update-and-view.md。
#
# 使い方:  bash refresh.sh
# ─────────────────────────────────────────────────────────────────
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
echo "▶ 現在のブランチ: $BRANCH"

# ── 生成物以外の未コミット変更があれば中断（誤って上書きしないため）──
# reset --hard で破棄してよい生成物のみを除外し、残りがあれば止める。
GENERATED_RE='^(index\.html|20[0-9]{4}\.html|publish_meta\.json|plan_[0-9]{6}\.json)$'
DIRTY="$(git status --porcelain | awk '{print $2}' | grep -vE "$GENERATED_RE" || true)"
if [ -n "$DIRTY" ]; then
  echo "⚠️ 生成物以外の未コミット変更があります。安全のため中断します:" >&2
  echo "$DIRTY" | sed 's/^/    /' >&2
  echo "   → 退避（git stash）またはコミットしてから再実行してください。" >&2
  exit 1
fi

echo "▶ リモート最新を取得して同期…"
git fetch origin "$BRANCH"
git reset --hard FETCH_HEAD
# 今後 git pull だけで済むよう upstream を設定（失敗しても致命的でない）
git branch --set-upstream-to="origin/$BRANCH" >/dev/null 2>&1 || true
echo "✓ 同期完了: $(git log --oneline -1)"

echo "▶ 動いている古いサーバーを停止…"
launchctl unload "$HOME/Library/LaunchAgents/com.casa.strava-report-server.plist" 2>/dev/null || true
PORT="${REPORT_SERVER_PORT:-8766}"
lsof -ti :"$PORT" 2>/dev/null | xargs kill 2>/dev/null || true
sleep 1

echo "▶ サーバーを起動（新コードで HTML を焼き直します）…"
export REPORT_SERVER_HOST="${REPORT_SERVER_HOST:-auto}"
exec python3 serve_report.py --open

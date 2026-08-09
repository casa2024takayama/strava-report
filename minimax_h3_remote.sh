#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# minimax_h3_remote.sh — Mac から Windows 上の MiniMax H3 を開く／起動する
#
#   1) Windows の ComfyUI がもう動いていれば → Mac のブラウザで開くだけ
#   2) 動いていなければ → SSH で Windows 側の minimax_h3.ps1 -Lan を実行し、
#      起動を待ってから Mac のブラウザで開く
#
# 使い方:  bash minimax_h3_remote.sh
#
# 設定（.env に書くか環境変数で指定）:
#   MINIMAX_H3_HOST      Windows のホスト名/IP（Tailscale 名や 100.x.y.z を推奨）〔必須〕
#   MINIMAX_H3_PORT      ComfyUI のポート（既定 8188）
#   MINIMAX_H3_SSH       SSH 接続先。省略時は "$MINIMAX_H3_HOST"（例 user@100.x.y.z）
#   MINIMAX_H3_WIN_DIR   Windows 側でこのリポジトリを clone したパス（既定 strava-report）
#
# 前提（初回のみ、Windows 側で）: docs/minimax-h3.md の「Mac からのリモート起動」参照
#   ・OpenSSH サーバーを有効化（設定 > システム > オプション機能 > OpenSSH サーバー）
#   ・このリポジトリを clone しておく（minimax_h3.ps1 を使うため）
# ─────────────────────────────────────────────────────────────────
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"

# .env から設定を読み込む（既存の環境変数を優先）
if [ -f "$DIR/.env" ]; then
  while IFS='=' read -r k v; do
    case "$k" in MINIMAX_H3_*) export "$k"="${!k:-$v}";; esac
  done < <(grep -E '^MINIMAX_H3_[A-Z_]+=' "$DIR/.env" || true)
fi

HOST="${MINIMAX_H3_HOST:-}"
PORT="${MINIMAX_H3_PORT:-8188}"
SSH_DEST="${MINIMAX_H3_SSH:-$HOST}"
WIN_DIR="${MINIMAX_H3_WIN_DIR:-strava-report}"

if [ -z "$HOST" ]; then
  echo "エラー: MINIMAX_H3_HOST が未設定です。" >&2
  echo "  .env に MINIMAX_H3_HOST=<WindowsのTailscale名かIP> を追記してください。" >&2
  echo "  （例: MINIMAX_H3_HOST=win-desktop / MINIMAX_H3_SSH=casa@win-desktop）" >&2
  exit 1
fi

URL="http://$HOST:$PORT"

alive() { curl -s -o /dev/null --max-time 3 "$URL/system_stats"; }

# ── 1) もう動いていれば開くだけ ──────────────────────────────────
if alive; then
  echo "✓ ComfyUI は起動済みです → $URL"
  open "$URL"
  exit 0
fi

# ── 2) SSH でリモート起動 ────────────────────────────────────────
echo "▶ $SSH_DEST に SSH して MiniMax H3 を起動します…"
if ! ssh -o ConnectTimeout=8 "$SSH_DEST" \
    "powershell -NoProfile -ExecutionPolicy Bypass -File \"$WIN_DIR\\minimax_h3.ps1\" -Lan -NoBrowser"; then
  echo "エラー: リモート起動に失敗しました。" >&2
  echo "  ・Windows が起動していてスリープしていないか" >&2
  echo "  ・OpenSSH サーバーが有効か（Windows 側: Get-Service sshd）" >&2
  echo "  ・MINIMAX_H3_WIN_DIR（現在: $WIN_DIR）が正しいか" >&2
  echo "  を確認してください。詳細は docs/minimax-h3.md。" >&2
  exit 1
fi

# ── 起動を待って Mac のブラウザで開く ────────────────────────────
echo "▶ 応答を待っています…"
for _ in $(seq 1 30); do
  if alive; then
    echo "✓ 起動完了 → $URL"
    echo "  ワークフロー: Template Library（テンプレート）> Video > MiniMax H3"
    open "$URL"
    exit 0
  fi
  sleep 2
done

echo "エラー: 起動はしたようですが $URL が応答しません。" >&2
echo "  Windows ファイアウォールで ComfyUI(python) の受信が許可されているか確認してください。" >&2
exit 1

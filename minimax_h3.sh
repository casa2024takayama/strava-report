#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# minimax_h3.sh — MiniMax H3（ComfyUI 動画生成）をワンコマンドで起動
#
#   1) ComfyUI のインストール先を自動検出（COMFYUI_DIR で上書き可）
#   2) MiniMax H3 のモデルファイルが揃っているか確認（無ければ案内）
#   3) すでにサーバーが動いていればブラウザを開くだけ
#   4) 動いていなければ起動 → 準備完了を待ってブラウザを開く
#
# 使い方:
#   bash minimax_h3.sh            # 起動してブラウザを開く
#   bash minimax_h3.sh --update   # ComfyUI を更新してから起動
#
# 環境変数:
#   COMFYUI_DIR   ComfyUI のパス（省略時は自動検出）
#   COMFYUI_PORT  ポート（既定 8188）
#   COMFYUI_ARGS  ComfyUI への追加引数（例: "--force-fp16"）
#
# 詳細・初回セットアップは docs/minimax-h3.md を参照。
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

PORT="${COMFYUI_PORT:-8188}"
URL="http://127.0.0.1:${PORT}"

open_browser() {
  if command -v open >/dev/null 2>&1; then open "$1"; else xdg-open "$1" 2>/dev/null || true; fi
}

# ── 1) ComfyUI の場所を探す ──────────────────────────────────────
if [ -z "${COMFYUI_DIR:-}" ]; then
  for d in "$HOME/ComfyUI" "$HOME/comfy/ComfyUI" "$HOME/Documents/ComfyUI" \
           "$HOME/dev/ComfyUI" "$HOME/AI/ComfyUI" "$HOME/git/ComfyUI"; do
    if [ -f "$d/main.py" ]; then COMFYUI_DIR="$d"; break; fi
  done
fi
if [ -z "${COMFYUI_DIR:-}" ] || [ ! -f "$COMFYUI_DIR/main.py" ]; then
  echo "エラー: ComfyUI が見つかりません。" >&2
  echo "  COMFYUI_DIR=/path/to/ComfyUI bash minimax_h3.sh のように指定してください。" >&2
  echo "  未インストールなら docs/minimax-h3.md の手順を参照してください。" >&2
  exit 1
fi
echo "▶ ComfyUI: $COMFYUI_DIR"

# ── ComfyUI 用 Python（venv があればそれを使う）─────────────────
PYTHON="python3"
for v in "$COMFYUI_DIR/.venv" "$COMFYUI_DIR/venv"; do
  if [ -x "$v/bin/python3" ]; then PYTHON="$v/bin/python3"; break; fi
done

# ── --update: ComfyUI 本体を更新（0.30.0 以降が MiniMax H3 対応）──
if [ "${1:-}" = "--update" ]; then
  echo "▶ ComfyUI を更新中…"
  git -C "$COMFYUI_DIR" pull --ff-only
  "$PYTHON" -m pip install -q -r "$COMFYUI_DIR/requirements.txt"
  echo "✓ 更新完了"
fi

# ── 2) MiniMax H3 のモデルファイル確認 ───────────────────────────
MODELS="$COMFYUI_DIR/models"
MISSING=""
ls "$MODELS/diffusion_models/"minimax_h3_*.safetensors >/dev/null 2>&1 || MISSING="$MISSING\n    models/diffusion_models/  … minimax_h3_*（拡散モデル）"
ls "$MODELS/text_encoders/"*minimax_h3*.safetensors "$MODELS/text_encoders/"qwen3vl*.safetensors >/dev/null 2>&1 || MISSING="$MISSING\n    models/text_encoders/     … qwen3vl_*_minimax_h3_*（テキストエンコーダ）"
ls "$MODELS/vae/"minimax_h3_*vae*.safetensors >/dev/null 2>&1 || MISSING="$MISSING\n    models/vae/               … minimax_h3_video_vae / audio_vae"
if [ -n "$MISSING" ]; then
  echo "⚠️  MiniMax H3 のモデルファイルが見つかりません:"
  printf '%b\n' "$MISSING"
  echo "   → ComfyUI のテンプレート（Template Library > Video > MiniMax H3）を開くと"
  echo "     不足モデルのダウンロードを案内してくれます（手動配置は docs/minimax-h3.md 参照）。"
  echo "   そのまま起動を続けます…"
fi

# ── 3) すでに動いていればブラウザを開くだけ ──────────────────────
if curl -s -o /dev/null --max-time 2 "$URL/system_stats"; then
  echo "✓ ComfyUI はすでに起動しています → $URL"
  open_browser "$URL"
  exit 0
fi

# ポートを掴んだまま応答しない古いプロセスがいれば停止
lsof -ti :"$PORT" 2>/dev/null | xargs kill 2>/dev/null || true

# ── 4) 起動 → 準備完了を待つ → ブラウザ ─────────────────────────
LOG="${TMPDIR:-/tmp}/comfyui-minimax-h3.log"
echo "▶ ComfyUI を起動します（ログ: $LOG）…"
cd "$COMFYUI_DIR"
# shellcheck disable=SC2086
"$PYTHON" main.py --listen 127.0.0.1 --port "$PORT" ${COMFYUI_ARGS:-} >"$LOG" 2>&1 &
PID=$!
trap 'kill "$PID" 2>/dev/null || true' INT TERM

for _ in $(seq 1 60); do
  if curl -s -o /dev/null --max-time 2 "$URL/system_stats"; then
    echo "✓ 起動完了 → $URL"
    echo "  ワークフロー: Template Library（テンプレート）> Video > MiniMax H3"
    open_browser "$URL"
    echo "  終了するには Ctrl+C"
    wait "$PID"
    exit 0
  fi
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "エラー: ComfyUI が起動に失敗しました。ログ末尾:" >&2
    tail -n 20 "$LOG" >&2
    exit 1
  fi
  sleep 1
done

echo "エラー: 60秒待っても応答がありません。ログ: $LOG" >&2
kill "$PID" 2>/dev/null || true
exit 1

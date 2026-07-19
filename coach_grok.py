#!/usr/bin/env python3
"""
AI マラソンコーチ（Grok / xAI API 版）— 比較専用バックエンド

Claude（本番・夜間23時）と同じ練習データ・同じプロンプトで Grok に講評させ、
2つのコーチングコメントを比較するためのスクリプト。

本番との違い（重要）:
  - 出力は既定で coaching_report_YYYYMM_grok.md（本番 md を上書きしない）
  - last_coach.json / plan_YYYYMM.json には一切書き込まない（update_cache=False）
  → レポートの「最終AI評価」表示・プランタブは Claude 由来のまま保たれる。

使い方:
  python3 coach_grok.py
  python3 coach_grok.py --month 2026-07
  （比較ページまで一括生成するなら → python3 coach_compare.py --run）

環境変数:
  XAI_API_KEY      必須（https://console.x.ai で発行。GROK_API_KEY でも可）
  GROK_MODEL       既定 grok-4（API から「モデルが無い」と言われたら
                   grok-4-fast / grok-3 等に変更する）
  GROK_TIMEOUT     既定 300（秒）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from coach_common import (
    build_training_summary,
    build_user_prompt,
    load_csv,
    load_env,
    resolve_month,
    save_coaching_report,
    validate_coaching_response,
    SYSTEM_PROMPT,
)

load_env()

XAI_API_KEY = os.environ.get("XAI_API_KEY", "") or os.environ.get("GROK_API_KEY", "")
GROK_MODEL = os.environ.get("GROK_MODEL", "grok-4")
GROK_TIMEOUT = int(os.environ.get("GROK_TIMEOUT", "300"))
XAI_API_URL = "https://api.x.ai/v1/chat/completions"


def run_coaching_grok(summary_text: str, year: int, month: int) -> str:
    if not XAI_API_KEY:
        print("エラー: XAI_API_KEY が未設定です。", file=sys.stderr)
        print("  https://console.x.ai で API キーを発行し、", file=sys.stderr)
        print("  .env に XAI_API_KEY=xai-... を追記してください。", file=sys.stderr)
        sys.exit(1)

    user_prompt = build_user_prompt(summary_text, year, month)
    # xAI API は OpenAI 互換の chat/completions
    body = {
        "model": GROK_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 8192,
    }

    print(f"\n🏃 AI マラソンコーチ（Grok / {GROK_MODEL}）")
    print("─" * 50)
    print("[💭 分析中...]\n")

    req = urllib.request.Request(
        XAI_API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {XAI_API_KEY}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=GROK_TIMEOUT) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"エラー: xAI API ({exc.code})\n{detail}", file=sys.stderr)
        if exc.code == 404 or "model" in detail.lower():
            print(f"  ヒント: GROK_MODEL={GROK_MODEL} が使えない可能性。"
                  "GROK_MODEL=grok-4-fast 等を試してください。", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"エラー: xAI API に接続できません\n  {exc}", file=sys.stderr)
        sys.exit(1)
    except TimeoutError:
        print(f"エラー: タイムアウト（{GROK_TIMEOUT}秒）", file=sys.stderr)
        sys.exit(1)

    choices = data.get("choices") or []
    if not choices:
        print(f"エラー: 応答が空です\n{data}", file=sys.stderr)
        sys.exit(1)

    text = ((choices[0].get("message") or {}).get("content") or "").strip()
    finish_reason = choices[0].get("finish_reason")

    if not text:
        print(f"エラー: テキスト応答がありません\n{data}", file=sys.stderr)
        sys.exit(1)

    print(text)
    print()

    # 比較用途なので書式ずれは警告のみ（本番と違い exit しない）
    done_reason = "length" if finish_reason == "length" else None
    warnings = validate_coaching_response(text, done_reason=done_reason)
    for warning in warnings:
        print(f"⚠️  {warning}（比較用のため続行）", file=sys.stderr)

    return text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Grok 版マラソンコーチ（比較専用）")
    parser.add_argument("--month", help="対象月 YYYY-MM（省略時は当月）")
    parser.add_argument("--runs", help="runs CSV パス（上書き）")
    parser.add_argument("--laps", help="laps CSV パス（上書き）")
    parser.add_argument("--output", "-o", help="出力 Markdown パス（上書き）")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    year, month, runs_csv, laps_csv, output_md = resolve_month(args.month)
    runs_csv = args.runs or runs_csv
    laps_csv = args.laps or laps_csv
    # 既定は比較用の別ファイル（本番 coaching_report_YYYYMM.md を上書きしない）
    output_md = args.output or output_md.replace(".md", "_grok.md")

    runs = load_csv(runs_csv)
    laps = load_csv(laps_csv)
    if not runs:
        print(f"エラー: {runs_csv} が見つかりません。先に strava_fetch.py を実行してください。")
        sys.exit(1)

    print(f"📂 {len(runs)} 件のランニング、{len(laps)} ラップを読み込みました")
    print(f"🤖 xAI API / {GROK_MODEL}（比較専用・本番には影響しません）")

    summary = build_training_summary(runs, laps, year, month)
    response = run_coaching_grok(summary, year, month)
    model_label = f"{GROK_MODEL}（xAI API）"
    coached_label = save_coaching_report(
        year=year,
        month=month,
        summary=summary,
        response=response,
        model_label=model_label,
        output_md=output_md,
        update_cache=False,  # 比較専用: last_coach.json / plan_*.json に触れない
    )

    print(f"✓ Grok コーチング完了 — {coached_label}")
    print(f"✓ {output_md} に保存しました（比較用）")


if __name__ == "__main__":
    main()

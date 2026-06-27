#!/usr/bin/env python3
"""
AI マラソンコーチ（Gemini API 版）— ダニエルズのランニングフォーミュラ準拠

runs_YYYYMM.csv を読み込み、Gemini が練習内容をレビューします。
GitHub Actions から毎日自動実行する想定。

使い方:
  python3 coach_gemini.py
  python3 coach_gemini.py --month 2026-06

環境変数:
  GEMINI_API_KEY   必須（Google AI Studio で発行）
  GEMINI_MODEL     既定 gemini-3.5-flash（AI Studio のプルダウンと別。API で指定）
  GEMINI_TIMEOUT   既定 180（秒）
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

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
GEMINI_TIMEOUT = int(os.environ.get("GEMINI_TIMEOUT", "180"))
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"


def validate_api_key_format(api_key: str) -> None:
    if api_key.startswith(("AIza", "AQ.")):
        return
    print("エラー: GEMINI_API_KEY の形式が正しくありません。", file=sys.stderr)
    print("  AI Studio で表示されたキー全体をコピーしてください。", file=sys.stderr)
    print("  正しいキーは AIza... または AQ.... で始まります。", file=sys.stderr)
    print("  変数名（GEMINI_API_KEY）や説明文を貼っていないか確認してください。", file=sys.stderr)
    sys.exit(1)


def run_coaching_gemini(summary_text: str, year: int, month: int) -> str:
    if not GEMINI_API_KEY:
        print("エラー: GEMINI_API_KEY が未設定です。", file=sys.stderr)
        print("  Google AI Studio → Get API key で取得し、", file=sys.stderr)
        print("  .env または GitHub Secrets に設定してください。", file=sys.stderr)
        sys.exit(1)
    validate_api_key_format(GEMINI_API_KEY)

    user_prompt = build_user_prompt(summary_text, year, month)
    url = f"{GEMINI_API_BASE}/models/{GEMINI_MODEL}:generateContent"
    body = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 8192,
        },
    }

    print(f"\n🏃 AI マラソンコーチ（Gemini / {GEMINI_MODEL}）")
    print("─" * 50)
    print("[💭 分析中...]\n")

    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": GEMINI_API_KEY,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=GEMINI_TIMEOUT) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"エラー: Gemini API ({exc.code})\n{detail}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"エラー: Gemini API に接続できません\n  {exc}", file=sys.stderr)
        sys.exit(1)
    except TimeoutError:
        print(f"エラー: タイムアウト（{GEMINI_TIMEOUT}秒）", file=sys.stderr)
        sys.exit(1)
    candidates = data.get("candidates") or []
    if not candidates:
        print(f"エラー: 応答が空です\n{data}", file=sys.stderr)
        sys.exit(1)

    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts).strip()
    finish_reason = candidates[0].get("finishReason")

    if not text:
        print(f"エラー: テキスト応答がありません\n{data}", file=sys.stderr)
        sys.exit(1)

    print(text)
    print()

    done_reason = "length" if finish_reason == "MAX_TOKENS" else None
    warnings = validate_coaching_response(text, done_reason=done_reason)
    if warnings:
        for warning in warnings:
            print(f"⚠️  {warning}", file=sys.stderr)
        sys.exit(1)

    return text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gemini 版マラソンコーチ")
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
    output_md = args.output or output_md

    runs = load_csv(runs_csv)
    laps = load_csv(laps_csv)
    if not runs:
        print(f"エラー: {runs_csv} が見つかりません。先に strava_fetch.py を実行してください。")
        sys.exit(1)

    print(f"📂 {len(runs)} 件のランニング、{len(laps)} ラップを読み込みました")
    print(f"🤖 Gemini API / {GEMINI_MODEL}")

    summary = build_training_summary(runs, laps, year, month)
    response = run_coaching_gemini(summary, year, month)
    model_label = f"{GEMINI_MODEL}（Gemini API）"
    coached_label = save_coaching_report(
        year=year,
        month=month,
        summary=summary,
        response=response,
        model_label=model_label,
        output_md=output_md,
    )

    print(f"✓ AI コーチング完了 — {coached_label}")
    print(f"✓ {output_md} に保存しました")


if __name__ == "__main__":
    main()

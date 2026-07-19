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

バックエンドは2種類（GROK_BACKEND で選択）:
  hermes  … Hermes CLI（`hermes chat -q "…" -Q`）経由。SuperGrok の OAuth/サブスク枠で
            動くため API クレジット不要（追加費用ゼロ）。Hermes が動く Mac 専用。
  api     … xAI API 直叩き。XAI_API_KEY と console.x.ai のクレジット残高が必要。

環境変数:
  GROK_BACKEND     hermes / api（既定 api。.env に GROK_BACKEND=hermes と書けば固定）
  HERMES_CMD       Hermes CLI のコマンド名（既定 hermes）
  XAI_API_KEY      api バックエンドで必須（https://console.x.ai で発行。GROK_API_KEY でも可）
  GROK_MODEL       api: 既定 grok-4 ／ hermes: 表示ラベル用（実モデルは Hermes 設定に従う）
  GROK_TIMEOUT     既定 300（秒）
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
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
GROK_BACKEND = os.environ.get("GROK_BACKEND", "api").strip().lower()
HERMES_CMD = os.environ.get("HERMES_CMD", "hermes")
GROK_MODEL = os.environ.get("GROK_MODEL", "grok-4")
GROK_TIMEOUT = int(os.environ.get("GROK_TIMEOUT", "300"))
XAI_API_URL = "https://api.x.ai/v1/chat/completions"


def run_coaching_hermes(summary_text: str, year: int, month: int) -> str:
    """Hermes CLI（SuperGrok OAuth）経由で Grok に講評させる。追加課金なし。"""
    user_prompt = build_user_prompt(summary_text, year, month)
    # Hermes の chat -q には --system が無いので1つのプロンプトに結合する
    combined = f"System: {SYSTEM_PROMPT}\n\nUser: {user_prompt}"

    print(f"\n🏃 AI マラソンコーチ（Grok / Hermes 経由・SuperGrok）")
    print("─" * 50)
    print("[💭 分析中...]\n")

    try:
        result = subprocess.run(
            [HERMES_CMD, "chat", "-q", combined, "-Q"],
            capture_output=True, text=True, timeout=GROK_TIMEOUT,
        )
    except FileNotFoundError:
        print(f"エラー: {HERMES_CMD} コマンドが見つかりません。", file=sys.stderr)
        print("  Hermes が入った Mac で実行するか、GROK_BACKEND=api に切り替えてください。",
              file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print(f"エラー: Hermes がタイムアウト（{GROK_TIMEOUT}秒）", file=sys.stderr)
        sys.exit(1)

    if result.returncode != 0:
        print(f"エラー: Hermes が失敗しました (exit {result.returncode})", file=sys.stderr)
        if result.stderr.strip():
            print(result.stderr.strip()[-2000:], file=sys.stderr)
        sys.exit(1)

    text = result.stdout.strip()
    if not text:
        print("エラー: Hermes の応答が空です", file=sys.stderr)
        sys.exit(1)

    print(text)
    print()

    # 比較用途なので書式ずれは警告のみ
    warnings = validate_coaching_response(text)
    for warning in warnings:
        print(f"⚠️  {warning}（比較用のため続行）", file=sys.stderr)

    return text


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

    summary = build_training_summary(runs, laps, year, month)
    if GROK_BACKEND == "hermes":
        print("🤖 Grok / Hermes 経由（SuperGrok・追加課金なし・本番には影響しません）")
        response = run_coaching_hermes(summary, year, month)
        model_label = f"{os.environ.get('GROK_MODEL', 'grok')}（Hermes / SuperGrok）"
    else:
        print(f"🤖 xAI API / {GROK_MODEL}（比較専用・本番には影響しません）")
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

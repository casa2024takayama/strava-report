#!/usr/bin/env python3
"""
AI マラソンコーチ（Claude API 版）— ダニエルズのランニングフォーミュラ準拠

runs_YYYYMM.csv を読み込み、Claude が練習内容をレビューします。
GitHub Actions から毎日自動実行する想定。

使い方:
  python3 coach_claude.py
  python3 coach_claude.py --month 2026-06
  python3 coach_claude.py --force   # キャッシュ無視で再生成

環境変数:
  ANTHROPIC_API_KEY   必須（https://console.anthropic.com/）
  CLAUDE_MODEL         既定 claude-sonnet-4-6
  CLAUDE_THINKING      既定 disabled（adaptive で思考オン＝高コスト）
  CLAUDE_MAX_TOKENS    既定 16384（1回の応答上限）
  CLAUDE_TIMEOUT       既定 300（秒）
  COACH_FORCE=1        キャッシュ無視（--force と同じ）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime

from coach_common import (
    SYSTEM_PROMPT,
    build_training_summary,
    build_user_prompt,
    compute_runs_data_hash,
    load_csv,
    load_env,
    resolve_month,
    save_coaching_report,
    validate_coaching_response,
)

load_env()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "") or os.environ.get("CLAUDE_API_KEY", "")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
CLAUDE_THINKING = os.environ.get("CLAUDE_THINKING", "disabled").strip().lower()
CLAUDE_MAX_TOKENS = int(os.environ.get("CLAUDE_MAX_TOKENS", "16384"))
CLAUDE_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT", "300"))
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


def cache_path(yyyymm: str) -> str:
    return f"coach_cache_{yyyymm}.json"


def compute_data_hash(runs_csv: str, laps_csv: str) -> str:
    return compute_runs_data_hash(runs_csv, laps_csv)


def load_cache(yyyymm: str) -> dict | None:
    path = cache_path(yyyymm)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def save_cache(
    *,
    yyyymm: str,
    data_hash: str,
    report_md: str,
    run_count: int,
) -> None:
    payload = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "data_hash": data_hash,
        "model": CLAUDE_MODEL,
        "thinking": CLAUDE_THINKING,
        "report": report_md,
        "run_count": run_count,
    }
    with open(cache_path(yyyymm), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def should_skip(
    *,
    yyyymm: str,
    data_hash: str,
    output_md: str,
    force: bool,
) -> str | None:
    """スキップする場合は理由文字列、生成する場合は None。"""
    if force:
        return None
    cached = load_cache(yyyymm)
    if not cached:
        return None
    if cached.get("data_hash") != data_hash:
        return None
    if not os.path.exists(output_md):
        return None
    label = cached.get("at", "不明")
    return f"走行データに変更なし — キャッシュ利用（前回: {label}）"


def validate_api_key_format(api_key: str) -> None:
    if api_key.startswith("sk-ant-"):
        return
    print("エラー: ANTHROPIC_API_KEY の形式が正しくありません。", file=sys.stderr)
    print("  sk-ant- で始まるキーを https://console.anthropic.com/ からコピーしてください。", file=sys.stderr)
    sys.exit(1)


def run_coaching_claude(summary_text: str, year: int, month: int) -> str:
    if not ANTHROPIC_API_KEY:
        print("エラー: ANTHROPIC_API_KEY が未設定です。", file=sys.stderr)
        print("  https://console.anthropic.com/ → API Keys で取得し、", file=sys.stderr)
        print("  .env または GitHub Secrets に設定してください。", file=sys.stderr)
        sys.exit(1)
    validate_api_key_format(ANTHROPIC_API_KEY)

    user_prompt = build_user_prompt(summary_text, year, month)
    body: dict = {
        "model": CLAUDE_MODEL,
        "max_tokens": CLAUDE_MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    if CLAUDE_THINKING == "adaptive":
        body["thinking"] = {"type": "adaptive"}
    else:
        body["thinking"] = {"type": "disabled"}

    print(f"\n🏃 AI マラソンコーチ（Claude / {CLAUDE_MODEL}）")
    print(f"   thinking={CLAUDE_THINKING}  max_tokens={CLAUDE_MAX_TOKENS}")
    print("─" * 50)
    print("[💭 分析中...]\n")

    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": ANTHROPIC_VERSION,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=CLAUDE_TIMEOUT) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"エラー: Claude API ({exc.code})\n{detail}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"エラー: Claude API に接続できません\n  {exc}", file=sys.stderr)
        sys.exit(1)
    except TimeoutError:
        print(f"エラー: タイムアウト（{CLAUDE_TIMEOUT}秒）", file=sys.stderr)
        sys.exit(1)

    content = data.get("content") or []
    text = "".join(block.get("text", "") for block in content if block.get("type") == "text").strip()
    stop_reason = data.get("stop_reason")

    if not text:
        print(f"エラー: テキスト応答がありません\n{data}", file=sys.stderr)
        sys.exit(1)

    print(text)
    print()

    done_reason = "length" if stop_reason == "max_tokens" else None
    warnings = validate_coaching_response(text, done_reason=done_reason)
    if warnings:
        for warning in warnings:
            print(f"⚠️  {warning}", file=sys.stderr)
        sys.exit(1)

    return text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Claude 版マラソンコーチ")
    parser.add_argument("--month", help="対象月 YYYY-MM（省略時は当月）")
    parser.add_argument("--runs", help="runs CSV パス（上書き）")
    parser.add_argument("--laps", help="laps CSV パス（上書き）")
    parser.add_argument("--output", "-o", help="出力 Markdown パス（上書き）")
    parser.add_argument("--force", action="store_true", help="キャッシュを無視して再生成")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    year, month, runs_csv, laps_csv, output_md = resolve_month(args.month)
    runs_csv = args.runs or runs_csv
    laps_csv = args.laps or laps_csv
    output_md = args.output or output_md
    yyyymm = f"{year}{month:02d}"
    force = args.force or os.environ.get("COACH_FORCE", "").lower() in ("1", "true", "yes")

    runs = load_csv(runs_csv)
    laps = load_csv(laps_csv)

    if not runs:
        print(f"⏭️  走行データなし（{runs_csv}）— Claude API はスキップします")
        sys.exit(0)

    data_hash = compute_data_hash(runs_csv, laps_csv)
    skip_reason = should_skip(
        yyyymm=yyyymm,
        data_hash=data_hash,
        output_md=output_md,
        force=force,
    )
    if skip_reason:
        print(f"⏭️  {skip_reason}")
        print(f"   → 既存の {output_md} をそのまま利用します")
        sys.exit(0)

    print(f"📂 {len(runs)} 件のランニング、{len(laps)} ラップを読み込みました")
    print(f"🤖 Claude API / {CLAUDE_MODEL}")

    summary = build_training_summary(runs, laps, year, month)
    response = run_coaching_claude(summary, year, month)
    model_label = f"{CLAUDE_MODEL}（Claude API）"
    coached_label = save_coaching_report(
        year=year,
        month=month,
        summary=summary,
        response=response,
        model_label=model_label,
        output_md=output_md,
    )
    save_cache(
        yyyymm=yyyymm,
        data_hash=data_hash,
        report_md=output_md,
        run_count=len(runs),
    )

    print(f"✓ AI コーチング完了 — {coached_label}")
    print(f"✓ {output_md} に保存しました")
    print(f"✓ {cache_path(yyyymm)} にキャッシュを保存しました")


if __name__ == "__main__":
    main()

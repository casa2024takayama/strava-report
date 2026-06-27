#!/usr/bin/env python3
"""
AI マラソンコーチ（Ollama / gemma4 版）— ダニエルズのランニングフォーミュラ準拠

runs_YYYYMM.csv と runs_YYYYMM_laps.csv を読み込み、
ローカル Ollama（既定: gemma4:12b）が練習内容をレビューします。

使い方:
  python3 coach_ollama.py
  python3 coach_ollama.py --month 2026-06
  OLLAMA_MODEL=gemma4:12b python3 coach_ollama.py

環境変数:
  OLLAMA_HOST        既定 http://127.0.0.1:11434
  OLLAMA_MODEL       既定 gemma4:12b
  OLLAMA_TIMEOUT     既定 3600（秒）
  OLLAMA_NUM_PREDICT 既定 8192
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date

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

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:12b")
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "3600"))
OLLAMA_NUM_PREDICT = int(os.environ.get("OLLAMA_NUM_PREDICT", "8192"))


def check_ollama() -> None:
    url = f"{OLLAMA_HOST}/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            resp.read()
    except urllib.error.URLError as e:
        print(f"エラー: Ollama に接続できません ({OLLAMA_HOST})\n  {e}", file=sys.stderr)
        print("  Ollama.app を起動するか、OLLAMA_HOST を確認してください。", file=sys.stderr)
        sys.exit(1)


def build_chat_body(user_prompt: str, *, stream: bool) -> dict:
    return {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "stream": stream,
        "think": False,
        "options": {"num_predict": OLLAMA_NUM_PREDICT},
    }


def extract_message_text(data: dict) -> str:
    msg = data.get("message") or {}
    content = (msg.get("content") or "").strip()
    if content:
        return content
    return (msg.get("thinking") or "").strip()


def run_coaching_ollama(summary_text: str, year: int, month: int, *, stream: bool = True) -> str:
    user_prompt = build_user_prompt(summary_text, year, month)
    body = build_chat_body(user_prompt, stream=stream)
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    print(f"\n🏃 AI マラソンコーチ（Ollama / {OLLAMA_MODEL}）")
    print(f"   num_predict={OLLAMA_NUM_PREDICT}, think=false")
    print("─" * 50)
    print("[💭 分析中... 1〜3分かかることがあります]\n")

    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
            if not stream:
                data = json.load(resp)
                text = extract_message_text(data)
                done_reason = data.get("done_reason")
                print(text)
                warnings = validate_coaching_response(text, done_reason=done_reason)
                if warnings:
                    for w in warnings:
                        print(f"⚠️  {w}", file=sys.stderr)
                    sys.exit(1)
                return text

            chunks: list[str] = []
            done_reason: str | None = None
            for raw_line in resp:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                data = json.loads(line)
                content = (data.get("message") or {}).get("content", "")
                if content:
                    print(content, end="", flush=True)
                    chunks.append(content)
                if data.get("done"):
                    done_reason = data.get("done_reason")
                    break
            print("\n")
            text = "".join(chunks)
            warnings = validate_coaching_response(text, done_reason=done_reason)
            if warnings:
                for w in warnings:
                    print(f"⚠️  {w}", file=sys.stderr)
                sys.exit(1)
            return text
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print(f"エラー: Ollama API ({e.code})\n{err_body}", file=sys.stderr)
        sys.exit(1)
    except TimeoutError:
        print("エラー: タイムアウト。gemma4 は thinking モデルのため時間がかかります。", file=sys.stderr)
        print(f"  OLLAMA_TIMEOUT={OLLAMA_TIMEOUT} を増やすか、モデルを軽量化してください。", file=sys.stderr)
        sys.exit(1)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ollama 版マラソンコーチ")
    p.add_argument("--month", help="対象月 YYYY-MM（省略時は当月）")
    p.add_argument("--runs", help="runs CSV パス（上書き）")
    p.add_argument("--laps", help="laps CSV パス（上書き）")
    p.add_argument("--output", "-o", help="出力 Markdown パス（上書き）")
    p.add_argument("--no-stream", action="store_true", help="ストリーミング出力を無効化")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    year, month, runs_csv, laps_csv, output_md = resolve_month(args.month)
    runs_csv = args.runs or runs_csv
    laps_csv = args.laps or laps_csv
    output_md = args.output or output_md

    check_ollama()

    runs = load_csv(runs_csv)
    laps = load_csv(laps_csv)
    if not runs:
        print(f"エラー: {runs_csv} が見つかりません。先に strava_fetch.py を実行してください。")
        sys.exit(1)

    print(f"📂 {len(runs)} 件のランニング、{len(laps)} ラップを読み込みました")
    print(f"🤖 {OLLAMA_HOST} / {OLLAMA_MODEL}")

    summary = build_training_summary(runs, laps, year, month)
    response = run_coaching_ollama(summary, year, month, stream=not args.no_stream)
    model_label = f"{OLLAMA_MODEL}（Ollama ローカル）"
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

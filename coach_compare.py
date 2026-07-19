#!/usr/bin/env python3
"""
Claude と Grok のコーチング講評を並べて比較するページを生成する。

前提:
  - Claude 版: coaching_report_YYYYMM.md（本番。🤖AIボタン or 夜間23時で生成済み）
  - Grok 版:   coaching_report_YYYYMM_grok.md（coach_grok.py で生成）

使い方:
  python3 coach_compare.py --run     # Grok 生成 → 比較ページ作成（推奨・一括）
  python3 coach_compare.py           # 既存の2つの md から比較ページのみ再生成
  python3 coach_compare.py --month 2026-07

出力:
  compare_YYYYMM.html（自己完結・ローカルサーバーが配信、スマホからも閲覧可）
  ※ publish.sh の git add 対象（index.html 20*.html …）に一致しないため公開されない。
"""

from __future__ import annotations

import argparse
import html
import os
import subprocess
import sys

from coach_common import load_env, parse_coach_meta_from_md, resolve_month

load_env()
PORT = os.environ.get("REPORT_SERVER_PORT", "8766")


def _coach_body(md_path: str) -> str:
    """md から「## コーチングレビュー」以降の本文を取り出す（無ければ全文）。"""
    with open(md_path, encoding="utf-8") as f:
        text = f.read()
    marker = "## コーチングレビュー"
    idx = text.find(marker)
    return text[idx + len(marker):].strip() if idx >= 0 else text.strip()


def _meta_line(md_path: str) -> str:
    meta = parse_coach_meta_from_md(md_path)
    if not meta:
        return "生成日不明"
    return f"{meta['label']}（{meta['model']}）"


def _advert_host() -> str:
    """閲覧URL表示用。Tailscale IP が取れればそれ、無ければ 127.0.0.1。"""
    try:
        out = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip().splitlines()
        if out and out[0].strip():
            return out[0].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "127.0.0.1"


def build_compare_html(yyyymm: str, claude_md: str, grok_md: str, out_html: str) -> None:
    claude_body = html.escape(_coach_body(claude_md))
    grok_body = html.escape(_coach_body(grok_md))
    claude_meta = html.escape(_meta_line(claude_md))
    grok_meta = html.escape(_meta_line(grok_md))
    title = f"AIコーチ比較 — {yyyymm[:4]}年{int(yyyymm[4:])}月"

    page = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: -apple-system, "Hiragino Sans", sans-serif;
         background: #f0efe9; color: #1c1917; }}
  header {{ position: sticky; top: 0; background: #fff; padding: 12px 16px;
            border-bottom: 2px solid #FC4C02; }}
  header h1 {{ margin: 0; font-size: 17px; }}
  header p {{ margin: 4px 0 0; font-size: 12px; color: #78716c; }}
  .grid {{ display: grid; gap: 14px; padding: 14px; max-width: 1200px; margin: 0 auto;
           grid-template-columns: 1fr; }}
  @media (min-width: 900px) {{ .grid {{ grid-template-columns: 1fr 1fr; }} }}
  .col {{ background: #fff; border-radius: 14px; overflow: hidden;
          box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  .col h2 {{ margin: 0; padding: 10px 14px; font-size: 14px; color: #fff; }}
  .col .meta {{ padding: 6px 14px; font-size: 11.5px; color: #78716c;
                border-bottom: 1px solid #e7e5e4; }}
  .col.claude h2 {{ background: #b45309; }}
  .col.grok h2 {{ background: #1c1917; }}
  .body {{ padding: 12px 14px; font-size: 13.5px; line-height: 1.75;
           white-space: pre-wrap; overflow-x: auto; }}
</style>
</head>
<body>
<header>
  <h1>🤖 {title}</h1>
  <p>同じ練習データ・同じプロンプトに対する2モデルの講評比較（ローカル限定・非公開）</p>
</header>
<div class="grid">
  <div class="col claude">
    <h2>Claude（本番）</h2>
    <div class="meta">{claude_meta}</div>
    <div class="body">{claude_body}</div>
  </div>
  <div class="col grok">
    <h2>Grok（比較）</h2>
    <div class="meta">{grok_meta}</div>
    <div class="body">{grok_body}</div>
  </div>
</div>
</body>
</html>
"""
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(page)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Claude と Grok の講評比較ページを生成")
    parser.add_argument("--month", help="対象月 YYYY-MM（省略時は当月）")
    parser.add_argument("--run", action="store_true",
                        help="Grok コーチングを実行してから比較ページを作る")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    year, month, _runs, _laps, claude_md = resolve_month(args.month)
    yyyymm = f"{year}{month:02d}"
    grok_md = claude_md.replace(".md", "_grok.md")
    out_html = f"compare_{yyyymm}.html"

    if not os.path.exists(claude_md):
        print(f"エラー: {claude_md} がありません（Claude 本番の講評が未生成）。")
        print("  先に 🤖AI ボタン、または夜間23時の自動実行で生成してください。")
        sys.exit(1)

    if args.run:
        cmd = [sys.executable, "coach_grok.py"]
        if args.month:
            cmd += ["--month", args.month]
        print("▶ Grok コーチングを実行します…")
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print("エラー: coach_grok.py が失敗しました。", file=sys.stderr)
            sys.exit(1)

    if not os.path.exists(grok_md):
        print(f"エラー: {grok_md} がありません。")
        print("  python3 coach_compare.py --run で Grok 生成から実行してください。")
        sys.exit(1)

    build_compare_html(yyyymm, claude_md, grok_md, out_html)
    print(f"✓ 比較ページを生成: {out_html}")
    print(f"  閲覧: http://{_advert_host()}:{PORT}/{out_html}")


if __name__ == "__main__":
    main()

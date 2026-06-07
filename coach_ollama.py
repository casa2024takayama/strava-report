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
  OLLAMA_HOST   既定 http://127.0.0.1:11434
  OLLAMA_MODEL  既定 gemma4:12b
"""

from __future__ import annotations

import argparse
import calendar
import csv
import json
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date

# ── .env 読み込み ──────────────────────────────────────────────────────────
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:12b")
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "1800"))

SYSTEM_PROMPT = """あなたはダニエルズのランニングフォーミュラ（Jack Daniels' Running Formula）に精通した、
経験豊富なマラソンコーチです。

## コーチとしての知識基盤

### VDOT システム
- VDOT はランナーの現在の走力を示す指標（VO2max の近似値）
- レースタイムや練習データから推定可能
- ペースと心拍数の関係から有酸素能力を評価する

### トレーニング強度ゾーン（ダニエルズ 5ゾーン）
| ゾーン | 名称 | 目的 | HR目安 | ペース感覚 |
|--------|------|------|--------|-----------|
| E (Easy) | イージー走 | 有酸素基礎・回復 | HRmax の 59-74% | 余裕で会話できる |
| M (Marathon) | マラソンペース走 | マラソン特異的適応 | HRmax の 75-84% | 少し努力が必要 |
| T (Threshold) | テンポ走 | 乳酸閾値向上 | HRmax の 83-88% | 「しんどいが維持できる」 |
| I (Interval) | インターバル走 | VO2max 向上 | HRmax の 97-100% | 非常に苦しい・3〜5分継続 |
| R (Repetition) | レペティション | スピード・走経済性 | 最大強度 | 全力に近い短距離 |

### ダニエルズの主要原則
1. **80/20 ルール**: 週間練習量の約 80% はイージー、20% がクオリティ（T/I/R）
2. **クオリティセッション**: 週2回が上限（回復を優先）
3. **長距離走の上限**: 週間距離の 25-30%、かつ 2.5〜3 時間を超えない
4. **距離の段階的増加**: 週3-4週ごとに 10% 以上増やさない
5. **ストレス→適応サイクル**: ハード練習の翌日は必ずイージーまたは休養

### レビュー時の観点
- 練習の質と量のバランス（クオリティが多すぎないか）
- 週間距離の推移（急激な増加がないか）
- ペースと心拍数の整合性
- 回復の確保（連日高強度になっていないか）
- ロング走の実施状況
- ラップデータから見えるペース配分

## レビュースタイル
- 日本語で回答
- データに基づいた具体的なフィードバック
- 良かった点と改善点を両方指摘
- 翌月の練習提案も含める
- 励ましながらも的確に問題点を指摘する
"""


def load_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def resolve_month(month: str | None) -> tuple[int, int, str, str, str]:
    if month:
        year_s, mon_s = month.split("-", 1)
        year, mon = int(year_s), int(mon_s)
    else:
        today = date.today()
        year, mon = today.year, today.month
    yyyymm = f"{year}{mon:02d}"
    runs_csv = f"runs_{yyyymm}.csv"
    laps_csv = f"runs_{yyyymm}_laps.csv"
    output_md = f"coaching_report_{yyyymm}.md"
    return year, mon, runs_csv, laps_csv, output_md


def build_training_summary(runs: list[dict], laps: list[dict], year: int, month: int) -> str:
    lines = [f"## {year}年{month}月 ランニングデータ\n"]
    month_start = date(year, month, 1)

    total_dist = sum(float(r["distance_km"] or 0) for r in runs)
    avg_hrs = [float(r["avg_heartrate"]) for r in runs if r.get("avg_heartrate")]
    lines.append(f"- 総距離: {total_dist:.1f} km / {len(runs)} 回")
    if avg_hrs:
        lines.append(f"- 平均心拍数: {sum(avg_hrs) / len(avg_hrs):.0f} bpm")

    by_week: dict[int, float] = defaultdict(float)
    for r in runs:
        try:
            d = date.fromisoformat(r["date"])
            w = (d - month_start).days // 7 + 1
            by_week[w] += float(r["distance_km"] or 0)
        except Exception:
            pass
    lines.append("\n### 週別距離")
    for w in sorted(by_week):
        lines.append(f"- 第{w}週: {by_week[w]:.1f} km")

    lines.append("\n### アクティビティ一覧")
    lines.append("| 日付 | 曜 | 名前 | 距離 | ペース(/km) | 平均HR | 最大HR | 獲得標高 |")
    lines.append("|------|----|----|------|------------|--------|--------|---------|")
    for r in runs:
        lines.append(
            f"| {r['date']} | {r.get('weekday', '')} | {r['name']} "
            f"| {float(r['distance_km'] or 0):.1f}km "
            f"| {r.get('pace_per_km', '-')} "
            f"| {r.get('avg_heartrate', '-')} "
            f"| {r.get('max_heartrate', '-')} "
            f"| {float(r.get('elevation_gain_m') or 0):.0f}m |"
        )

    if laps:
        multi: dict[str, list] = defaultdict(list)
        for lap in laps:
            multi[lap["activity_id"]].append(lap)
        detail_laps = {aid: ls for aid, ls in multi.items() if len(ls) > 1}
        if detail_laps:
            lines.append("\n### ラップ詳細（複数ラップのみ）")
            for r in runs:
                aid = str(r["activity_id"])
                if aid not in detail_laps:
                    continue
                lines.append(f"\n**{r['date']} {r['name']}**")
                lines.append("| Lap | 距離 | ペース | HR |")
                lines.append("|-----|------|--------|-----|")
                for lap in detail_laps[aid]:
                    lines.append(
                        f"| {lap['lap_index']} | {float(lap['distance_km'] or 0):.2f}km "
                        f"| {lap.get('pace_per_km', '-')} | {lap.get('avg_heartrate', '-')} |"
                    )

    return "\n".join(lines)


def next_month_label(year: int, month: int) -> str:
    if month == 12:
        return f"{year + 1}年1月"
    return f"{year}年{month + 1}月"


def build_user_prompt(summary_text: str, year: int, month: int) -> str:
    nxt = next_month_label(year, month)
    return f"""{summary_text}

---

上記の{year}年{month}月の練習データについて、以下の観点でレビューとアドバイスをお願いします：

1. **月間総評** — 量・質・バランスの評価
2. **週別評価** — 各週の練習内容と課題
3. **個別アクティビティコメント** — 特筆すべき練習（良い点・改善点）
4. **ペース・心拍分析** — VDOT 推定とゾーン配分の評価
5. **{nxt}の練習提案** — ダニエルズメソッドに基づく具体的なプラン
"""


def check_ollama() -> None:
    url = f"{OLLAMA_HOST}/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            resp.read()
    except urllib.error.URLError as e:
        print(f"エラー: Ollama に接続できません ({OLLAMA_HOST})\n  {e}", file=sys.stderr)
        print("  Ollama.app を起動するか、OLLAMA_HOST を確認してください。", file=sys.stderr)
        sys.exit(1)


def run_coaching_ollama(summary_text: str, year: int, month: int, *, stream: bool = True) -> str:
    user_prompt = build_user_prompt(summary_text, year, month)
    body = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "stream": stream,
    }
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    print(f"\n🏃 AI マラソンコーチ（Ollama / {OLLAMA_MODEL}）")
    print("─" * 50)
    print("[💭 分析中... 1〜3分かかることがあります]\n")

    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
            if not stream:
                data = json.load(resp)
                text = data.get("message", {}).get("content", "")
                print(text)
                return text

            chunks: list[str] = []
            for raw_line in resp:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                data = json.loads(line)
                content = data.get("message", {}).get("content", "")
                if content:
                    print(content, end="", flush=True)
                    chunks.append(content)
                if data.get("done"):
                    break
            print("\n")
            return "".join(chunks)
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

    today = date.today()
    content = f"# マラソンコーチングレポート — {year}年{month}月\n\n"
    content += f"生成日: {today}  /  モデル: {OLLAMA_MODEL}（Ollama ローカル）\n\n"
    content += "## 練習データ\n\n" + summary + "\n\n---\n\n"
    content += "## コーチングレビュー\n\n" + response

    with open(output_md, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"✓ {output_md} に保存しました")


if __name__ == "__main__":
    main()

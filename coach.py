#!/usr/bin/env python3
"""
AI マラソンコーチ — ダニエルズのランニングフォーミュラ準拠
runs_march2026.csv と runs_march2026_laps.csv を読み込み、
Claude Opus 4.6 が練習内容をレビューしてコメントを返します
"""

import csv
import os
import sys
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

import anthropic

RUNS_CSV  = "runs_march2026.csv"
LAPS_CSV  = "runs_march2026_laps.csv"
OUTPUT_MD = "coaching_report_march2026.md"

# ── データ読み込み ─────────────────────────────────────────────────────────
def load_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def build_training_summary(runs, laps):
    """CSV データをコーチへ渡すテキストサマリーに変換"""
    lines = ["## 2026年3月 ランニングデータ\n"]

    # 全体サマリー
    total_dist = sum(float(r["distance_km"] or 0) for r in runs)
    avg_hrs = [float(r["avg_heartrate"]) for r in runs if r.get("avg_heartrate")]
    lines.append(f"- 総距離: {total_dist:.1f} km / {len(runs)} 回")
    if avg_hrs:
        lines.append(f"- 平均心拍数: {sum(avg_hrs)/len(avg_hrs):.0f} bpm")

    # 週別距離
    by_week = defaultdict(float)
    for r in runs:
        try:
            d = date.fromisoformat(r["date"])
            w = (d - date(2026, 3, 1)).days // 7 + 1
            by_week[w] += float(r["distance_km"] or 0)
        except Exception: pass
    lines.append("\n### 週別距離")
    for w in sorted(by_week):
        lines.append(f"- 第{w}週: {by_week[w]:.1f} km")

    # 個別アクティビティ
    lines.append("\n### アクティビティ一覧")
    lines.append("| 日付 | 曜 | 名前 | 距離 | ペース(/km) | 平均HR | 最大HR | 獲得標高 |")
    lines.append("|------|----|----|------|------------|--------|--------|---------|")
    for r in runs:
        lines.append(
            f"| {r['date']} | {r.get('weekday','')} | {r['name']} "
            f"| {float(r['distance_km'] or 0):.1f}km "
            f"| {r.get('pace_per_km','-')} "
            f"| {r.get('avg_heartrate','-')} "
            f"| {r.get('max_heartrate','-')} "
            f"| {float(r.get('elevation_gain_m') or 0):.0f}m |"
        )

    # ラップデータ（複数ラップのみ）
    if laps:
        multi = defaultdict(list)
        for lap in laps: multi[lap["activity_id"]].append(lap)
        detail_laps = {aid: ls for aid, ls in multi.items() if len(ls) > 1}
        if detail_laps:
            lines.append("\n### ラップ詳細（複数ラップのみ）")
            for r in runs:
                aid = str(r["activity_id"])
                if aid not in detail_laps: continue
                lines.append(f"\n**{r['date']} {r['name']}**")
                lines.append("| Lap | 距離 | ペース | HR |")
                lines.append("|-----|------|--------|-----|")
                for lap in detail_laps[aid]:
                    lines.append(
                        f"| {lap['lap_index']} | {float(lap['distance_km'] or 0):.2f}km "
                        f"| {lap.get('pace_per_km','-')} | {lap.get('avg_heartrate','-')} |"
                    )

    return "\n".join(lines)

# ── システムプロンプト ─────────────────────────────────────────────────────
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

### 心拍数ゾーン（最大心拍数ベース）
- Zone 1 (回復): < 70% HRmax
- Zone 2 (有酸素): 70-80% HRmax
- Zone 3 (テンポ): 80-87% HRmax
- Zone 4 (閾値): 87-93% HRmax
- Zone 5 (VO2max): 93-100% HRmax

### ダニエルズの主要原則
1. **80/20 ルール**: 週間練習量の約 80% はイージー、20% がクオリティ（T/I/R）
2. **クオリティセッション**: 週2回が上限（回復を優先）
3. **長距離走の上限**: 週間距離の 25-30%、かつ 2.5〜3 時間を超えない
4. **距離の段階的増加**: 週3-4週ごとに 10% 以上増やさない
5. **ストレス→適応サイクル**: ハード練習の翌日は必ずイージーまたは休養

### レビュー時の観点
- 練習の質と量のバランス（クオリティが多すぎないか）
- 週間距離の推移（急激な増加がないか）
- ペースと心拍数の整合性（ペースに対して心拍が高すぎないか）
- 回復の確保（連日高強度になっていないか）
- ロング走の実施状況
- ラップデータから見えるペース配分（前半突っ込みすぎがないか）

## レビュースタイル
- 日本語で回答
- データに基づいた具体的なフィードバック
- 良かった点と改善点を両方指摘
- 次の4週間の練習提案も含める
- 励ましながらも的確に問題点を指摘する
"""

# ── コーチング実行 ─────────────────────────────────────────────────────────
def run_coaching(summary_text):
    client = anthropic.Anthropic()

    prompt = f"""{summary_text}

---

上記の2026年3月の練習データについて、以下の観点でレビューとアドバイスをお願いします：

1. **月間総評** — 量・質・バランスの評価
2. **週別評価** — 各週の練習内容と課題
3. **個別アクティビティコメント** — 特筆すべき練習（良い点・改善点）
4. **ペース・心拍分析** — VDOT 推定とゾーン配分の評価
5. **4月の練習提案** — ダニエルズメソッドに基づく具体的なプラン
"""

    print("\n🏃 AI マラソンコーチ（ダニエルズ式）によるレビュー")
    print("─" * 50)

    full_response = []

    with client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=64000,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        thinking_shown = False
        for event in stream:
            if event.type == "content_block_start":
                if event.content_block.type == "thinking" and not thinking_shown:
                    print("\n[💭 分析中...]\n")
                    thinking_shown = True
                elif event.content_block.type == "text":
                    if thinking_shown:
                        print()  # 思考後の改行
            elif event.type == "content_block_delta":
                if event.delta.type == "text_delta":
                    print(event.delta.text, end="", flush=True)
                    full_response.append(event.delta.text)

    print("\n")
    return "".join(full_response)

# ── メイン ─────────────────────────────────────────────────────────────────
def main():
    runs = load_csv(RUNS_CSV)
    laps = load_csv(LAPS_CSV)

    if not runs:
        print(f"エラー: {RUNS_CSV} が見つかりません。先に strava_fetch.py を実行してください。")
        sys.exit(1)

    print(f"📂 {len(runs)} 件のランニング、{len(laps)} ラップを読み込みました")
    summary = build_training_summary(runs, laps)

    response = run_coaching(summary)

    # Markdown として保存
    today = date.today()
    content = f"# マラソンコーチングレポート — 2026年3月\n\n生成日: {today}\n\n"
    content += "## 練習データ\n\n" + summary + "\n\n---\n\n"
    content += "## コーチングレビュー\n\n" + response

    with open(OUTPUT_MD, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"✓ {OUTPUT_MD} に保存しました")

if __name__ == "__main__":
    main()

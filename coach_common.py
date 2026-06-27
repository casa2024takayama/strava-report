"""マラソンコーチ共通ロジック（Ollama / Gemini / Claude 共有）"""

from __future__ import annotations

import csv
import json
import os
import re
from collections import defaultdict
from datetime import date, datetime

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
- **思考過程は出力せず、最終レビュー全文を回答として書くこと**
- 指定された5項目（月間総評・週別・個別・ペース分析・翌月提案）をすべて完結させること
"""

_COACH_META_RE = re.compile(
    r"生成日:\s*([^/\n]+)\s*/\s*モデル:\s*(.+)",
)


def load_env() -> None:
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def load_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def compute_runs_data_hash(runs_csv: str, laps_csv: str) -> str:
    import hashlib

    digest = hashlib.sha256()
    for path in (runs_csv, laps_csv):
        if os.path.exists(path):
            with open(path, "rb") as f:
                digest.update(f.read())
    # Garmin データも反映：変化したらコーチングを再生成する（回復・負荷を最新に保つ）
    try:
        import garmin
        gpath = garmin._csv_path()
        if gpath and os.path.exists(gpath):
            with open(gpath, "rb") as f:
                digest.update(f.read())
    except Exception:
        pass
    return digest.hexdigest()


def coaching_stale_detail(yyyymm: str, runs_csv: str, laps_csv: str) -> dict | None:
    """走行 CSV が AI 評価より新しい場合、差分情報を返す。"""
    runs = load_csv(runs_csv)
    if not runs:
        return None
    cache_path = f"coach_cache_{yyyymm}.json"
    current = len(runs)
    if not os.path.exists(cache_path):
        return {
            "current_runs": current,
            "coach_runs": 0,
            "message": f"AI 評価未生成（走行 {current} 件）",
        }
    try:
        with open(cache_path, encoding="utf-8") as f:
            cached = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"current_runs": current, "coach_runs": 0, "message": f"AI 評価未生成（走行 {current} 件）"}
    if cached.get("data_hash") == compute_runs_data_hash(runs_csv, laps_csv):
        return None
    coach_runs = cached.get("run_count", "?")
    return {
        "current_runs": current,
        "coach_runs": coach_runs,
        "message": f"AI 評価が古いです（{coach_runs} 件時点 → 現在 {current} 件）",
    }


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

    # Garmin の回復・負荷指標を追記（取得済みなら）。失敗してもサマリーは出す。
    try:
        import garmin
        garmin_block = garmin.build_garmin_summary(year, month)
        if garmin_block:
            lines.append("\n" + garmin_block)
    except Exception:
        pass

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

Garmin の回復・負荷指標（VO2max トレンド・トレーニングステータス・レディネス・HRV・睡眠・安静時心拍）が
データにある場合は必ず参照し、強度と回復のバランス、オーバーリーチの兆候、翌月プランの強度設定に反映してください。
"""


def validate_coaching_response(text: str, *, done_reason: str | None = None) -> list[str]:
    warnings: list[str] = []
    if done_reason == "length":
        warnings.append("トークン上限で打ち切られた可能性があります")
    if len(text) < 800:
        warnings.append(f"回答が短すぎます（{len(text)} 文字）")
    for needle, label in (("月間", "月間総評"), ("週", "週別評価")):
        if needle not in text:
            warnings.append(f"「{label}」らしき記述が見つかりません")
    if not any(k in text for k in ("翌月", "練習提案", "の練習提案")):
        warnings.append("翌月の練習提案らしき記述が見つかりません")
    tail = text.rstrip()
    if tail and tail[-1] not in "。．.!！?？\n）)」":
        warnings.append("文末が途中で切れている可能性があります")
    return warnings


def parse_coach_meta_from_md(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        for line in f.readlines()[:8]:
            match = _COACH_META_RE.search(line.strip())
            if match:
                return {"label": match.group(1).strip(), "model": match.group(2).strip()}
    return None


def save_coaching_report(
    *,
    year: int,
    month: int,
    summary: str,
    response: str,
    model_label: str,
    output_md: str,
) -> str:
    today = date.today()
    coached_at = datetime.now()
    coached_label = coached_at.strftime("%Y-%m-%d %H:%M:%S")

    content = f"# マラソンコーチングレポート — {year}年{month}月\n\n"
    content += f"生成日: {coached_label}  /  モデル: {model_label}\n\n"
    content += "## 練習データ\n\n" + summary + "\n\n---\n\n"
    content += "## コーチングレビュー\n\n" + response

    with open(output_md, "w", encoding="utf-8") as f:
        f.write(content)

    cache_dir = ".strava_cache"
    os.makedirs(cache_dir, exist_ok=True)
    with open(os.path.join(cache_dir, "last_coach.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "at": coached_at.isoformat(timespec="seconds"),
                "label": coached_label,
                "month": f"{year}{month:02d}",
                "model": model_label,
                "report": output_md,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    return coached_label

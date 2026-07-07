"""マラソンコーチ共通ロジック（Ollama / Gemini / Claude 共有）"""

from __future__ import annotations

import csv
import json
import os
import re
import calendar
from collections import defaultdict
from datetime import date, datetime

MONTHLY_DISTANCE_GOAL_KM = 200

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

### このランナーの固定目標（必ず守る）
- **月間走行距離目標は 200km**（レース当日の距離もこの合計に含める）
- **週間距離は月内でおおむね均等配分**（例: 5週の月なら約 40km/週）
- **レースがある月**は `races.json` 相当の登録レース日を必ず意識し、テーパー・回復・種目特性を織り込む
- 翌月の練習提案では、上記 200km とレース距離の両方を満たす週別・日別メニューを具体的に示すこと

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


# ── 前月 AI コーチング → 当月練習メニュー ────────────────────────────────────
# weekly row: (weekday 0=Mon, type_key, label, target_km, description)
WeeklyPlanRow = tuple[int, str, str, float, str]

_DAY_TO_WD = {"月": 0, "火": 1, "水": 2, "木": 3, "金": 4, "土": 5, "日": 6}

_WEEK_HEADER_RE = re.compile(
    r"####\s*(?:[✅🟡🔵🟠]\s*)?第[\d〜\-]+週[（(](\d+)/(\d+)〜(\d+)/(\d+)[）)]：(.+)$",
    re.MULTILINE,
)
_TARGET_KM_RE = re.compile(r"目標(?:距離\s*(?:各)?)?([\d〜\-]+)\s*km")
_DATE_CELL_RE = re.compile(r"^(\d+)/(\d+)$")


def prev_month_year_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def prev_month_label(year: int, month: int) -> str:
    py, pm = prev_month_year_month(year, month)
    return f"{py}年{pm}月"


def load_next_month_plan_markdown(year: int, month: int) -> str | None:
    """前月 coaching_report から当月分「練習提案」セクションを抽出。"""
    py, pm = prev_month_year_month(year, month)
    path = f"coaching_report_{py}{pm:02d}.md"
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        content = f.read()
    marker = "## コーチングレビュー"
    if marker in content:
        body = content.split(marker, 1)[1]
    else:
        body = content
    heading = f"## 5. {year}年{month}月の練習提案"
    if heading not in body:
        alt = re.search(rf"## 5\.\s*{year}年{month}月の練習提案", body)
        if not alt:
            return None
        start = alt.start()
    else:
        start = body.index(heading)
    rest = body[start + len(heading) :]
    end = re.search(r"\n## [^#]", rest)
    section = rest[: end.start()].strip() if end else rest.strip()
    return section or None


def _parse_km_token(text: str) -> float:
    text = text.replace("km", "").strip()
    if not text or text in ("—", "-", "－"):
        return 0.0
    nums = [float(x) for x in re.findall(r"[\d.]+", text)]
    if not nums:
        return 0.0
    if "〜" in text and len(nums) >= 2:
        return (nums[0] + nums[1]) / 2
    return nums[0]


def _plan_type_from_text(label: str, desc: str = "") -> str:
    t = f"{label} {desc}"
    if "休養" in t or "休" in label:
        return "rest"
    if "ロング" in t:
        return "long"
    if "Iペース" in t or "インターバル" in t:
        return "interval"
    if "Tペース" in t or "テンポ" in t or "クオリティ" in t:
        return "tempo"
    return "easy"


def _recovery_week_plan() -> list[WeeklyPlanRow]:
    desc = "6:00〜6:30/km・HR115-135・40-50分上限（クオリティなし）"
    easy = (0, "easy", "イージー走", 5.0, desc)
    return [
        easy,
        (1, "easy", "イージー走", 5.0, desc),
        (2, "rest", "休養", 0.0, "完全休養推奨"),
        (3, "easy", "イージー走", 5.0, desc),
        (4, "easy", "イージー走", 5.0, desc),
        (5, "easy", "イージー走", 5.0, desc),
        (6, "easy", "イージー走", 5.0, desc),
    ]


def _load_up_week_plan() -> list[WeeklyPlanRow]:
    return [
        (0, "rest", "休養", 0.0, "完全休養"),
        (1, "easy", "イージー走", 10.0, "6:00〜6:20/km"),
        (2, "tempo", "Tペース走", 10.0, "W-up 2km + テンポ20分(4:29-4:37) + CD 2km"),
        (3, "easy", "イージー走", 8.0, "回復ジョグ 6:20/km"),
        (4, "interval", "Iペース走", 8.0, "W-up 2km + 4:02-4:10/km×3-4分×4-5本 + CD 2km"),
        (5, "easy", "イージー走", 8.0, "6:10/km"),
        (6, "long", "ロング走", 23.0, "22-24km・Eペース厳守・ペースアップ禁止"),
    ]


def _parse_week_table(
    block: str, year: int
) -> tuple[list[WeeklyPlanRow] | None, dict[date, WeeklyPlanRow]]:
    dated: list[tuple[date, WeeklyPlanRow]] = []
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4 or cells[0] not in _DAY_TO_WD:
            continue
        wd = _DAY_TO_WD[cells[0]]
        date_m = _DATE_CELL_RE.match(cells[1])
        if date_m and len(cells) >= 5:
            dm, dd = int(date_m.group(1)), int(date_m.group(2))
            label, dist_cell = cells[2], cells[3]
            pace_desc = cells[5] if len(cells) > 5 and cells[5] else cells[4]
            on_date = date(year, dm, dd)
        else:
            label, dist_cell = cells[1], cells[2]
            pace_desc = cells[3] if len(cells) > 3 else ""
            on_date = None
        dist = _parse_km_token(dist_cell)
        ptype = _plan_type_from_text(label, pace_desc)
        short = label.split("（")[0].split("(")[0].strip()[:12]
        desc = pace_desc if pace_desc else label
        row: WeeklyPlanRow = (wd, ptype, short, dist, desc)
        if on_date:
            dated.append((on_date, row))
    if len(dated) < 3:
        return None, {}
    by_wd = {r[0]: r for _, r in dated}
    by_date = {d: r for d, r in dated}
    plan = [by_wd.get(i, (i, "rest", "休養", 0.0, "—")) for i in range(7)]
    return plan, by_date


def _week_range_contains(
    on_date: date, year: int, sm: int, sd: int, em: int, ed: int
) -> bool:
    start = date(year, sm, sd)
    end = date(year, em, ed)
    return start <= on_date <= end


def resolve_ai_weekly_plan(
    year: int, month: int, on_date: date | None = None
) -> dict | None:
    """前月 AI の当月練習提案から、指定日が属する週のメニューを返す。"""
    on_date = on_date or date.today()
    if on_date.year != year or on_date.month != month:
        return None
    plan_md = load_next_month_plan_markdown(year, month)
    if not plan_md:
        return None

    blocks = re.split(r"(?=####\s*(?:[✅🟡🔵🟠]\s*)?第)", plan_md)
    for block in blocks:
        m = _WEEK_HEADER_RE.search(block)
        if not m:
            continue
        sm, sd, em, ed = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        title_tail = m.group(5).strip()
        if not _week_range_contains(on_date, year, sm, sd, em, ed):
            continue
        km_m = _TARGET_KM_RE.search(title_tail)
        range_km = km_m.group(1) if km_m else "—"
        week_title = m.group(0).lstrip("#").strip()

        if "リカバリー" in title_tail or "イージー走のみ" in block:
            plan = _recovery_week_plan()
            plan_by_date: dict[date, WeeklyPlanRow] = {}
        elif "負荷アップ" in title_tail:
            plan = _load_up_week_plan()
            plan_by_date = {}
        else:
            parsed, plan_by_date = _parse_week_table(block, year)
            plan = parsed
        if not plan:
            continue
        return {
            "plan": plan,
            "plan_by_date": plan_by_date,
            "range_km": range_km,
            "week_title": week_title,
            "subtitle": f"前月 AI コーチ提案に基づく（{prev_month_label(year, month)}レビュー）",
            "source_month": prev_month_label(year, month),
        }
    return None


# ── 月間プラン（全週）構造化：markdown パース／JSON 抽出・保存 ─────────────────
_PLAN_ZONE_ENUM = {"E", "E+坂", "ロングE", "M", "T", "I", "R", "休養", "レース", "移動"}
# 直接コード（長いものから順に前方一致判定）
_ZONE_TOKENS_ORDERED = ("E+坂", "ロングE", "レース", "移動", "休養", "E", "M", "T", "I", "R")


def _normalize_zone(label: str, desc: str = "") -> str:
    """種別ラベル/内容から design のゾーンコードへ正規化。"""
    lab = (label or "").strip()
    for z in _ZONE_TOKENS_ORDERED:
        if lab == z or lab.startswith(z + " ") or lab.startswith(z + "：") \
           or lab.startswith(z + "(") or lab.startswith(z + "（"):
            return z
    t = f"{lab} {desc}"
    if "レース" in t:
        return "レース"
    if "移動" in t:
        return "移動"
    if "休" in lab:
        return "休養"
    if "ロング" in t:
        return "ロングE"
    if "Iペース" in t or "インターバル" in t:
        return "I"
    if "Mペース" in t or "マラソンペース" in t:
        return "M"
    if "Rペース" in t or "レペティ" in t:
        return "R"
    if "Tペース" in t or "テンポ" in t or "クオリティ" in t or "閾値" in t:
        return "T"
    return "E"


def parse_all_weeks_from_md(year: int, month: int) -> list[dict] | None:
    """前月 AI の当月練習提案 markdown から全週を構造化。失敗/空は None。

    返り値: [{num, range:"M/D〜M/D", theme, target_km, days:[{date,dow,zone,dist,desc}]}]
    """
    plan_md = load_next_month_plan_markdown(year, month)
    if not plan_md:
        return None
    blocks = re.split(r"(?=####\s*(?:[✅🟡🔵🟠]\s*)?第)", plan_md)
    weeks: list[dict] = []
    for block in blocks:
        m = _WEEK_HEADER_RE.search(block)
        if not m:
            continue
        sm, sd, em, ed = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        title_tail = m.group(5).strip()
        num_m = re.search(r"第(\d+)週", m.group(0))
        num = int(num_m.group(1)) if num_m else len(weeks) + 1
        km_m = _TARGET_KM_RE.search(title_tail)
        target_km = km_m.group(1) if km_m else ""
        theme = re.split(r"目標", title_tail)[0].strip("：: 　")
        days: list[dict] = []
        for line in block.splitlines():
            line = line.strip()
            if not line.startswith("|") or "---" in line:
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 4 or cells[0] not in _DAY_TO_WD:
                continue
            date_m = _DATE_CELL_RE.match(cells[1])
            if not (date_m and len(cells) >= 5):
                continue
            dm, dd = int(date_m.group(1)), int(date_m.group(2))
            label, dist_cell = cells[2], cells[3]
            desc = cells[5] if len(cells) > 5 and cells[5] else cells[4]
            days.append({
                "date": f"{dm}/{dd}",
                "dow": cells[0],
                "zone": _normalize_zone(label, desc),
                "dist": dist_cell or "—",
                "desc": desc or label,
            })
        if not days:
            continue
        weeks.append({
            "num": num,
            "range": f"{sm}/{sd}〜{em}/{ed}",
            "theme": theme or f"第{num}週",
            "target_km": target_km,
            "days": days,
        })
    return weeks or None


def extract_plan_json(text: str) -> dict | None:
    """AI 応答末尾の ```json フェンス（PLAN_JSON）を抽出・検証。失敗は None。"""
    fences = re.findall(r"```json\s*(\{.*?\})\s*```", text or "", re.DOTALL)
    if not fences:
        return None
    try:
        data = json.loads(fences[-1])
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    month = data.get("month")
    if not (isinstance(month, str) and re.match(r"^\d{4}-\d{2}$", month)):
        return None
    weeks = data.get("weeks")
    if not isinstance(weeks, list) or not weeks:
        return None
    clean_weeks: list[dict] = []
    for w in weeks:
        if not isinstance(w, dict):
            continue
        days_in = w.get("days")
        if not isinstance(days_in, list) or not days_in:
            continue
        clean_days: list[dict] = []
        for d in days_in:
            if not isinstance(d, dict):
                continue
            zone = str(d.get("zone", "")).strip()
            if zone not in _PLAN_ZONE_ENUM:
                zone = _normalize_zone(zone, str(d.get("desc", "")))
            clean_days.append({
                "date": str(d.get("date", "")).strip(),
                "dow": str(d.get("dow", "")).strip(),
                "zone": zone,
                "dist": (str(d.get("dist", "")).strip() or "—"),
                "desc": str(d.get("desc", "")).strip(),
            })
        if not clean_days:
            continue
        clean_weeks.append({
            "num": w.get("num") if isinstance(w.get("num"), int) else len(clean_weeks) + 1,
            "range": str(w.get("range", "")).strip(),
            "theme": str(w.get("theme", "")).strip(),
            "target_km": w.get("target_km", ""),
            "days": clean_days,
        })
    if not clean_weeks:
        return None
    return {
        "month": month,
        "goal_km": data.get("goal_km"),
        "overview": str(data.get("overview", "")).strip(),
        "weeks": clean_weeks,
    }


def write_month_plan(plan: dict) -> str | None:
    """検証済みプランを plan_<YYYYMM>.json に書き出す（健康データ非含有＝コミット可）。"""
    month = plan.get("month")
    if not month:
        return None
    path = f"plan_{month.replace('-', '')}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    return path


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


def next_month_year_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def load_races() -> list[tuple[date, str, float]]:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "races.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    out: list[tuple[date, str, float]] = []
    for row in data:
        out.append((date.fromisoformat(row["date"]), row["name"], float(row["dist_km"])))
    return sorted(out, key=lambda x: x[0])


def races_in_month(year: int, month: int) -> list[tuple[date, str, float]]:
    return [(d, n, k) for d, n, k in load_races() if d.year == year and d.month == month]


def weeks_in_month(year: int, month: int) -> int:
    ndays = calendar.monthrange(year, month)[1]
    return max(4, (ndays + 6) // 7)


def build_monthly_plan_constraints(year: int, month: int) -> str:
    """翌月提案用: 200km 均等配分 + レース考慮の必須条件。"""
    nweeks = weeks_in_month(year, month)
    weekly_km = round(MONTHLY_DISTANCE_GOAL_KM / nweeks, 1)
    races = races_in_month(year, month)
    wd_names = ["月", "火", "水", "木", "金", "土", "日"]

    lines = [
        f"### ランナーからの必須条件（{year}年{month}月の練習プラン）",
        f"- **月間走行距離目標: {MONTHLY_DISTANCE_GOAL_KM}km**（レース当日の距離もこの合計に含める）",
        f"- **週間目標は均等配分**: おおむね **{weekly_km}km/週**"
        f"（{nweeks}週想定 · {MONTHLY_DISTANCE_GOAL_KM}km ÷ {nweeks}）",
        "- 週別提案では**各週の目標kmを明示**し、合計が月間200km前後になるよう整合させること",
        "- 日別メニュー（曜日テーブル）も可能な限り示すこと",
    ]
    if races:
        lines.append("- **登録済みレース**（日程を必ず意識。テーパー・回復・種目特性を反映）:")
        race_km_total = 0.0
        for d, name, dist in races:
            race_km_total += dist
            lines.append(
                f"  - **{d.month}/{d.day}（{wd_names[d.weekday()]}）** {name} — **{dist:.1f}km**"
            )
        other_km = MONTHLY_DISTANCE_GOAL_KM - race_km_total
        lines.append(
            f"  - レース合計 {race_km_total:.1f}km は月間{MONTHLY_DISTANCE_GOAL_KM}kmに**含める**。"
            f"練習走の目安合計: 約 **{other_km:.1f}km**（残りを{nweeks}週で均等配分）"
        )
        lines.append("- **レース週**: ダニエルズ式テーパー（走行量↓・神経系の鋭さ維持）。レース前3〜7日はクオリティ控えめ")
        if any("富士登山" in n or "登山" in n for _, n, _ in races):
            lines.append(
                "- **富士登山競走**: 登坂持久・下り筋負荷・暑期の水分/塩分・装備試走。"
                "レース距離21.4kmを月間200kmに算入し、レース前週から量を段階的に落とす"
            )
    else:
        lines.append(
            f"- 当月の登録レースなし — 練習走のみで **{MONTHLY_DISTANCE_GOAL_KM}km** を週{nweeks}等分"
        )
    return "\n".join(lines)


def build_user_prompt(summary_text: str, year: int, month: int) -> str:
    nxt = next_month_label(year, month)
    ny, nm = next_month_year_month(year, month)
    nxt_iso = f"{ny}-{nm:02d}"
    plan_constraints = build_monthly_plan_constraints(ny, nm)

    # ── 現在日付の考慮（月の途中なら「経過日数に対するペース」で評価させる）──
    today = date.today()
    total_days = calendar.monthrange(year, month)[1]
    if today.year == year and today.month == month:
        elapsed = today.day
        pct = round(elapsed / total_days * 100)
        expected_km = round(MONTHLY_DISTANCE_GOAL_KM * pct / 100)
        date_context = (
            f"### ⏱ 重要：本日は {today.isoformat()}。{year}年{month}月は**まだ途中**です"
            f"（{total_days}日中 {elapsed}日経過・進捗 {pct}%）。\n"
            f"- 月間走行距離は**経過日数に対するペース**で評価すること。"
            f"目安：進捗{pct}%なら {MONTHLY_DISTANCE_GOAL_KM}km × {pct}% ≈ **{expected_km}km** が現時点のペース基準。\n"
            f"- **月末時点の目標（{MONTHLY_DISTANCE_GOAL_KM}km）との単純比較で『不足』と断定しないこと。**"
            f"「現ペースなら月末に約○km見込み（目標比△）」の形で述べる。\n"
            f"- 残り {total_days - elapsed} 日を踏まえた現実的な助言にすること。\n"
        )
    else:
        date_context = (
            f"### 本日は {today.isoformat()}。{year}年{month}月は**終了済み**です。"
            f"月全体の確定データとして総括・評価してください。\n"
        )

    return f"""{summary_text}

---

{date_context}
上記の{year}年{month}月の練習データについて、以下の観点でレビューとアドバイスをお願いします：

1. **月間総評** — 量・質・バランスの評価（月間200km目標との比較。**ただし上の日付注記に従い、月途中なら経過ペースで評価**）
2. **週別評価** — 各週の練習内容と課題
3. **個別アクティビティコメント** — 特筆すべき練習（良い点・改善点）
4. **ペース・心拍分析** — VDOT 推定とゾーン配分の評価
5. **{nxt}の練習提案** — ダニエルズメソッドに基づく具体的なプラン
   - 必ず **週別目標km**（均等配分）と **日別メニュー** を示すこと
   - レースがある場合は **レース日を軸にした4週間以上のフェーズ分け**（準備→テーパー→レース→回復）

{plan_constraints}

Garmin の回復・負荷指標（VO2max トレンド・トレーニングステータス・レディネス・HRV・睡眠・安静時心拍）が
データにある場合は必ず参照し、強度と回復のバランス、オーバーリーチの兆候、翌月プランの強度設定に反映してください。

### 出力書式（第5項＝{nxt}の練習提案は以下を厳守。自動解析に使用します）
- 見出しは必ず **`## 5. {nxt}の練習提案`**（この文字列のまま）
- 各週の見出しは **`#### 第N週（M/D〜M/D）：<週テーマ> 目標XXkm`**
  （括弧「（）」・チルダ「〜」・コロン「：」は**全角**、日付は `7/1` 形式）
- 各週に**日別テーブル**を付ける（列：`曜 | 日付 | 種別 | 距離 | 内容`、曜日は月〜日、日付は `7/1` 形式）
  例：
  ```
  #### 第1週（7/1〜7/6）：ベース再構築 目標40km
  | 曜 | 日付 | 種別 | 距離 | 内容 |
  |----|------|------|------|------|
  | 月 | 7/1 | E | 8km | イージー 6:00/km |
  ```

### 追加出力（回答の**最後**に、上の第5項と同じ内容を機械可読 JSON でも出力）
回答の一番最後に、以下スキーマの JSON を **```json フェンス1個だけ**で出力してください（プランタブの自動表示に使用。前後に説明文を付けない）：
```json
{{
  "month": "{nxt_iso}",
  "goal_km": 200,
  "overview": "月間方針を120字以内で",
  "weeks": [
    {{"num": 1, "range": "7/1〜7/6", "theme": "ベース再構築", "target_km": 40,
      "days": [{{"date": "7/1", "dow": "月", "zone": "E", "dist": "8km", "desc": "イージー 6:00/km"}}]}}
  ]
}}
```
- `zone` は必ず次のいずれか：`E` `E+坂` `ロングE` `M` `T` `I` `R` `休養` `レース` `移動`
- `weeks` は月内の全週、`days` は各週の全日（休養日も `zone`=`休養`・`dist`=`—` で必ず含める）
- `date` は `7/1` 形式、`dist` は表示用文字列（`8km` / `—`）。上の markdown 第5項と数値を一致させること
"""


def validate_coaching_response(text: str, *, done_reason: str | None = None) -> list[str]:
    """致命的な警告（内容が壊れている）を返す。保存を止めるべき問題のみ。"""
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
    return warnings


def check_coaching_response_soft(text: str) -> list[str]:
    """軽微な注意（保存はする・ログに出すだけ）。絵文字等での文末は正常とみなす。"""
    notes: list[str] = []
    tail = text.rstrip()
    if tail and not re.search(r"[。．.!！?？\n）)」]$", tail) and not re.search(r"[\U0001F300-\U0001FAFF☀-➿]$", tail):
        notes.append("文末が途中で切れている可能性があります（軽微・保存は継続）")
    return notes


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

    # ── 翌月プランの自動解析チェック（週次メニュー供給の要）──────────────
    # 生成結果が想定書式どおりか＝週次メニューが正しく抽出できるかを保存時に検査。
    try:
        ny, nm = next_month_year_month(year, month)
        plan_md = load_next_month_plan_markdown(ny, nm)
        if not plan_md:
            print(f"  ⚠️ 翌月プラン『## 5. {ny}年{nm}月の練習提案』が抽出できません"
                  f"（書式ずれ→週次メニューが固定プランにフォールバックします）")
        else:
            weeks = _WEEK_HEADER_RE.findall(plan_md)
            if len(weeks) < 3:
                print(f"  ⚠️ 週見出し『#### 第N週（M/D〜M/D）：…』が {len(weeks)} 個のみ"
                      f"（週次メニューが一部フォールバックする可能性）")
            else:
                print(f"  ✓ 翌月プラン解析OK（週見出し {len(weeks)} 個）")
    except Exception as _e:  # noqa: BLE001
        print(f"  ⚠️ 翌月プランの解析チェックに失敗: {_e}")

    # ── 月間プラン JSON（PLAN_JSON フェンス）の抽出・保存 ──────────────────
    # plan_<翌YYYYMM>.json を生成（プランタブの5週フル表示用。健康データ非含有）。
    try:
        plan = extract_plan_json(response)
        if plan:
            p = write_month_plan(plan)
            print(f"  ✓ 月間プラン JSON を保存: {p}（{len(plan['weeks'])} 週）")
        else:
            print("  ⚠️ PLAN_JSON フェンスなし/不正 — プランタブは markdown 全週パースにフォールバック")
    except Exception as _e:  # noqa: BLE001
        print(f"  ⚠️ 月間プラン JSON 抽出に失敗: {_e}")

    return coached_label

#!/usr/bin/env python3
"""
Strava 2026年3月 ランニングレポート生成
runs_march2026.csv と runs_march2026_laps.csv を読み込んでレポートを出力します
"""

import csv
import os
from collections import defaultdict
from datetime import date

RUNS_CSV = "runs_march2026.csv"
LAPS_CSV = "runs_march2026_laps.csv"
OUTPUT   = "report_march2026.md"

# ── ユーティリティ ─────────────────────────────────────────────────────────
def parse_time(t):
    """'5:32:10' or '45:30' → 秒"""
    if not t: return 0
    parts = list(map(int, str(t).split(":")))
    if len(parts) == 3: return parts[0]*3600 + parts[1]*60 + parts[2]
    if len(parts) == 2: return parts[0]*60 + parts[1]
    return int(parts[0])

def fmt_pace(sec_per_km):
    """秒/km → '5:32'"""
    if not sec_per_km: return "-"
    m, s = divmod(int(sec_per_km), 60)
    return f"{m}:{s:02d}"

def pace_to_sec(pace_str):
    """'5:32' → 秒/km"""
    if not pace_str: return None
    try:
        m, s = pace_str.split(":")
        return int(m)*60 + int(s)
    except Exception: return None

def week_number(date_str):
    """'2026-03-15' → 週番号（月曜始まり）"""
    try:
        d = date.fromisoformat(date_str)
        # 3月1日が属する週を1週目とする
        march1 = date(2026, 3, 1)
        days_diff = (d - march1).days
        return days_diff // 7 + 1
    except Exception: return 0

def load_runs():
    if not os.path.exists(RUNS_CSV):
        print(f"エラー: {RUNS_CSV} が見つかりません。先に strava_fetch.py を実行してください。")
        exit(1)
    with open(RUNS_CSV, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def load_laps():
    if not os.path.exists(LAPS_CSV):
        return []
    with open(LAPS_CSV, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

# ── 分析 ───────────────────────────────────────────────────────────────────
def analyze(runs, laps):
    total_dist = sum(float(r["distance_km"] or 0) for r in runs)
    total_time = sum(parse_time(r["moving_time"]) for r in runs)
    total_elev = sum(float(r["elevation_gain_m"] or 0) for r in runs)
    avg_hr     = [float(r["avg_heartrate"]) for r in runs if r.get("avg_heartrate")]
    avg_hr_val = sum(avg_hr) / len(avg_hr) if avg_hr else None

    # 週別集計
    by_week = defaultdict(lambda: {"runs": [], "dist": 0.0, "time": 0})
    for r in runs:
        w = week_number(r["date"])
        by_week[w]["runs"].append(r)
        by_week[w]["dist"] += float(r["distance_km"] or 0)
        by_week[w]["time"] += parse_time(r["moving_time"])

    # 最速・最遅ペース
    paces = [(r, pace_to_sec(r["pace_per_km"])) for r in runs if r.get("pace_per_km")]
    paces = [(r, p) for r, p in paces if p]
    fastest = min(paces, key=lambda x: x[1]) if paces else None
    slowest = max(paces, key=lambda x: x[1]) if paces else None

    # 最長ラン
    longest = max(runs, key=lambda r: float(r["distance_km"] or 0)) if runs else None

    return {
        "total_runs":  len(runs),
        "total_dist":  total_dist,
        "total_time":  total_time,
        "total_elev":  total_elev,
        "avg_hr":      avg_hr_val,
        "by_week":     dict(by_week),
        "fastest":     fastest,
        "slowest":     slowest,
        "longest":     longest,
    }

def estimate_training_type(run):
    """ペース・HR からトレーニング種別を推定"""
    pace = pace_to_sec(run.get("pace_per_km"))
    hr   = float(run.get("avg_heartrate") or 0)
    dist = float(run.get("distance_km") or 0)
    if not pace: return "不明"
    if dist >= 20: return "ロング走"
    if hr and hr >= 165: return "インターバル / I-pace"
    if hr and hr >= 155: return "テンポ走 / T-pace"
    if pace and pace <= 270: return "テンポ走 / T-pace"   # 4:30/km 以下
    return "イージー走 / E-pace"

# ── レポート生成 ───────────────────────────────────────────────────────────
def build_report(runs, laps, stats):
    lines = []
    def h(n, t): lines.append(f"\n{'#'*n} {t}\n")
    def p(*args): lines.append(" ".join(str(a) for a in args))
    def hr(): lines.append("\n---\n")

    h(1, "🏃 2026年3月 ランニングレポート")
    p(f"生成日: {date.today()}")
    hr()

    # ── サマリー
    h(2, "月間サマリー")
    t = stats["total_time"]
    hh, rem = divmod(t, 3600); mm = rem // 60
    p(f"| 項目 | 値 |")
    p(f"|------|-----|")
    p(f"| 総ランニング回数 | {stats['total_runs']} 回 |")
    p(f"| 総距離 | {stats['total_dist']:.1f} km |")
    p(f"| 総時間 | {hh}時間{mm}分 |")
    p(f"| 累積獲得標高 | {stats['total_elev']:.0f} m |")
    if stats["avg_hr"]: p(f"| 平均心拍数 | {stats['avg_hr']:.0f} bpm |")
    if stats["fastest"]:
        r, _ = stats["fastest"]
        p(f"| 最速ペース | {r['pace_per_km']} /km（{r['date']} {r['name']}）|")
    if stats["longest"]:
        r = stats["longest"]
        p(f"| 最長ラン | {float(r['distance_km']):.1f} km（{r['date']} {r['name']}）|")

    # ── 週別
    h(2, "週別サマリー")
    p("| 週 | 日数 | 距離 | 時間 |")
    p("|---|---|---|---|")
    for wk in sorted(stats["by_week"]):
        w = stats["by_week"][wk]
        t = w["time"]; hh, rem = divmod(t, 3600); mm = rem // 60
        p(f"| 第{wk}週 | {len(w['runs'])} 日 | {w['dist']:.1f} km | {hh}h{mm:02d}m |")

    # ── アクティビティ一覧
    h(2, "アクティビティ一覧")
    p("| 日付 | 曜日 | 名前 | 距離 | ペース | HR | 推定種別 |")
    p("|------|------|------|------|--------|-----|---------|")
    for r in runs:
        typ = estimate_training_type(r)
        hr_val = r.get("avg_heartrate") or "-"
        p(f"| {r['date']} | {r['weekday']} | {r['name']} "
          f"| {float(r['distance_km']):.1f}km | {r['pace_per_km'] or '-'} "
          f"| {hr_val} | {typ} |")

    # ── ラップ詳細（ラップが複数あるものだけ）
    if laps:
        multi_lap_ids = {r["activity_id"] for r in runs if int(r.get("total_laps") or 0) > 1}
        multi_laps = [l for l in laps if l["activity_id"] in multi_lap_ids]
        if multi_laps:
            h(2, "ラップ詳細（複数ラップのみ）")
            current_id = None
            for lap in multi_laps:
                if lap["activity_id"] != current_id:
                    current_id = lap["activity_id"]
                    p(f"\n**{lap['date']} {lap['activity_name']}**\n")
                    p("| Lap | 距離 | タイム | ペース | HR |")
                    p("|-----|------|--------|--------|-----|")
                hr_v = lap.get("avg_heartrate") or "-"
                p(f"| {lap['lap_index']} | {float(lap['distance_km'] or 0):.2f}km "
                  f"| {lap['moving_time']} | {lap['pace_per_km'] or '-'} | {hr_v} |")

    return "\n".join(lines)

# ── メイン ─────────────────────────────────────────────────────────────────
def main():
    print("📊 レポート生成中...")
    runs = load_runs()
    laps = load_laps()
    print(f"  {len(runs)} 件のランニング、{len(laps)} ラップを読み込みました")

    stats = analyze(runs, laps)
    report = build_report(runs, laps, stats)

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n✓ {OUTPUT} を生成しました")

    # ターミナルにも概要を表示
    print("\n─── 月間サマリー ──────────────────")
    print(f"  走行回数  : {stats['total_runs']} 回")
    print(f"  総距離    : {stats['total_dist']:.1f} km")
    t = stats["total_time"]; hh, rem = divmod(t, 3600); mm = rem // 60
    print(f"  総時間    : {hh}時間{mm}分")
    if stats["avg_hr"]: print(f"  平均心拍  : {stats['avg_hr']:.0f} bpm")
    print("────────────────────────────────────")
    print(f"\n詳細レポート → {OUTPUT}")
    print("コーチングレビュー → python3 coach.py")

if __name__ == "__main__":
    main()

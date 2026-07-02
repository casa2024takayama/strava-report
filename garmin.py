#!/usr/bin/env python3
"""
Garmin 生理指標をコーチングへ供給する読み取り専用モジュール
=========================================================
`garmin_daily.csv`（garmin_fetch.py が生成）を読み込み、対象月の
回復・負荷サマリー（Markdown）と最新 VO2max を返す。

取得（Garmin API ログイン）は別（~/Desktop/GarminConnect/garmin_fetch.py）。
ここは CSV を読むだけなので新規 pip 依存なし・online(Actions)でも動く。

CSV 解決順:
  1) 環境変数 GARMIN_DAILY_CSV
  2) リポジトリ直下 garmin_daily.csv（コミットして online で使う用）
  3) ~/Desktop/GarminConnect/garmin_daily.csv（ローカル開発用）

garmin_daily.csv の列:
  date, vo2max, readiness_score, readiness_level, hrv_last_night, hrv_status,
  sleep_score, sleep_hours, resting_hr, stress_avg, training_status, load_balance
"""

import csv
import os
from collections import Counter

_REPO_DIR = os.path.dirname(os.path.abspath(__file__))
_CANDIDATES = [
    os.environ.get("GARMIN_DAILY_CSV", ""),
    os.path.join(_REPO_DIR, "garmin_daily.csv"),
    os.path.expanduser("~/GarminConnect/garmin_daily.csv"),
    os.path.expanduser("~/Desktop/GarminConnect/garmin_daily.csv"),  # 旧場所（後方互換）
]


def _csv_path():
    for p in _CANDIDATES:
        if p and os.path.exists(p):
            return p
    return None


def load_garmin_daily():
    """garmin_daily.csv を list[dict] で返す（無ければ空リスト）。"""
    path = _csv_path()
    if not path:
        return []
    try:
        with open(path, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def _month_rows(rows, year, month):
    pref = f"{year}-{month:02d}"
    return [r for r in rows if (r.get("date") or "").startswith(pref)]


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _nums(rows, key):
    return [x for x in (_f(r.get(key)) for r in rows) if x is not None]


def latest_vo2max(rows=None):
    """最新（最終日付）の VO2max を返す。無ければ None。"""
    rows = rows if rows is not None else load_garmin_daily()
    for r in sorted(rows, key=lambda r: r.get("date", ""), reverse=True):
        v = _f(r.get("vo2max"))
        if v is not None:
            return v
    return None


def last_updated():
    """garmin_daily.csv の更新時刻（取得時刻の目安）。無ければ None。"""
    path = _csv_path()
    if not path:
        return None
    try:
        from datetime import datetime
        return datetime.fromtimestamp(os.path.getmtime(path))
    except Exception:
        return None


def recent_daily(n=14, year=None, month=None):
    """ダッシュボード表示用に直近 n 日（新しい順）の行を返す。
    year/month 指定時はその月に限定。何か値がある行のみ。"""
    rows = load_garmin_daily()
    if year and month:
        rows = _month_rows(rows, year, month)
    rows = [r for r in rows if any((r.get(k) or "").strip()
            for k in ("vo2max", "readiness_score", "hrv_last_night", "sleep_score", "resting_hr"))]
    rows = sorted(rows, key=lambda r: r.get("date", ""), reverse=True)
    return rows[:n]


def monthly_series(year, month):
    """チャート用：対象月の日付順に並んだ各指標の配列を返す。
    欠測は None（Chart.js が線を切る）。"""
    rows = sorted(_month_rows(load_garmin_daily(), year, month), key=lambda r: r.get("date", ""))
    keys = ("vo2max", "readiness_score", "hrv_last_night", "sleep_score", "resting_hr")
    series = {"dates": [], **{k: [] for k in keys}}
    for r in rows:
        series["dates"].append((r.get("date") or "")[5:])  # MM-DD
        for k in keys:
            series[k].append(_f(r.get(k)))
    # 全部Noneの指標は捨てる（空グラフ防止）。dates は残す。
    series = {k: v for k, v in series.items()
              if k == "dates" or any(x is not None for x in v)}
    return series if series.get("dates") else None


def build_garmin_summary(year, month):
    """対象月の Garmin 回復・負荷サマリー（Markdown）。データ無しは None。"""
    rows = _month_rows(load_garmin_daily(), year, month)
    rows = sorted(rows, key=lambda r: r.get("date", ""))
    # 何か中身がある行だけ
    rows = [r for r in rows if any((r.get(k) or "").strip()
            for k in ("vo2max", "readiness_score", "hrv_last_night", "sleep_score"))]
    if not rows:
        return None

    lines = ["## Garmin 生理指標（回復・負荷）",
             "※ コーチングでは練習データに加えてこの回復・負荷状況も考慮すること。"]

    # VO2max トレンド
    vo2 = [(r["date"], _f(r.get("vo2max"))) for r in rows if _f(r.get("vo2max")) is not None]
    if vo2:
        first_v, last_v = vo2[0][1], vo2[-1][1]
        arrow = "→" if abs(last_v - first_v) < 0.05 else ("↓" if last_v < first_v else "↑")
        note = "（低下傾向＝疲労/オーバーリーチの可能性）" if last_v < first_v - 0.3 else ""
        lines.append(f"- VO2max: {first_v:.1f} {arrow} {last_v:.1f}（現在 {last_v:.1f}・VDOT 目安）{note}")

    # トレーニングステータス / 負荷バランス（最新＋分布）
    statuses = [(r.get("training_status") or "").strip() for r in rows if (r.get("training_status") or "").strip()]
    if statuses:
        latest = statuses[-1]
        dist = ", ".join(f"{k}×{v}" for k, v in Counter(statuses).most_common())
        lines.append(f"- トレーニングステータス: 現在 **{latest}**（月内: {dist}）")
    balances = [(r.get("load_balance") or "").strip() for r in rows if (r.get("load_balance") or "").strip()]
    if balances:
        lines.append(f"- 負荷バランス: 現在 {balances[-1]}")

    # レディネス
    rd = _nums(rows, "readiness_score")
    if rd:
        lines.append(f"- トレーニングレディネス: 平均 {sum(rd)/len(rd):.0f} / 範囲 {min(rd):.0f}〜{max(rd):.0f}（100満点・高いほど高強度OK）")

    # HRV ステータス分布
    hrv_st = [(r.get("hrv_status") or "").strip() for r in rows if (r.get("hrv_status") or "").strip() and (r.get("hrv_status") or "").strip() != "NONE"]
    if hrv_st:
        c = Counter(hrv_st)
        dist = ", ".join(f"{k} {v}日" for k, v in c.most_common())
        lines.append(f"- HRVステータス: {dist}")
    hrv_v = _nums(rows, "hrv_last_night")
    if hrv_v:
        lines.append(f"- 夜間HRV: 平均 {sum(hrv_v)/len(hrv_v):.0f}ms / 範囲 {min(hrv_v):.0f}〜{max(hrv_v):.0f}ms")

    # 睡眠
    ss = _nums(rows, "sleep_score")
    sh = _nums(rows, "sleep_hours")
    if ss or sh:
        parts = []
        if ss: parts.append(f"スコア平均 {sum(ss)/len(ss):.0f}")
        if sh: parts.append(f"睡眠時間平均 {sum(sh)/len(sh):.1f}h")
        lines.append(f"- 睡眠: {' / '.join(parts)}")

    # 安静時心拍トレンド
    rhr = [(r["date"], _f(r.get("resting_hr"))) for r in rows if _f(r.get("resting_hr")) is not None]
    if rhr:
        f0, l0 = rhr[0][1], rhr[-1][1]
        lines.append(f"- 安静時心拍: {f0:.0f} → {l0:.0f} bpm（上昇は疲労蓄積のサイン）")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    from datetime import date
    if len(sys.argv) > 1:
        y, m = (int(x) for x in sys.argv[1].split("-"))
    else:
        t = date.today(); y, m = t.year, t.month
    print(f"CSV: {_csv_path()}")
    print(f"latest VO2max: {latest_vo2max()}")
    print("---")
    print(build_garmin_summary(y, m) or "(Garmin データなし)")

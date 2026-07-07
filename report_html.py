#!/usr/bin/env python3
"""
Strava HTML レポート生成（現在月を自動検出）
環境変数 TARGET_YEAR_MONTH=YYYY-MM で月を指定可能（省略時は当月）
"""

from __future__ import annotations

import csv, json, os, glob, re
import html as html_module
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import calendar as _cal_mod

from coach_common import (
    coaching_stale_detail,
    load_next_month_plan_markdown,
    parse_coach_meta_from_md,
    prev_month_label,
    resolve_ai_weekly_plan,
)

# ── .env 読み込み ──────────────────────────────────────────────────────────
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    with open(_env_path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

# ── 対象月の決定 ────────────────────────────────────────────────────────────
_ym_env = os.environ.get("TARGET_YEAR_MONTH", "")
if _ym_env:
    TARGET_YEAR  = int(_ym_env.split("-")[0])
    TARGET_MONTH = int(_ym_env.split("-")[1])
else:
    _today = date.today()
    TARGET_YEAR  = _today.year
    TARGET_MONTH = _today.month

YYYYMM       = f"{TARGET_YEAR}{TARGET_MONTH:02d}"
MONTH_LABEL  = f"{TARGET_YEAR}年{TARGET_MONTH}月"
MONTH_START  = date(TARGET_YEAR, TARGET_MONTH, 1)
MONTH_END    = date(TARGET_YEAR, TARGET_MONTH,
                    _cal_mod.monthrange(TARGET_YEAR, TARGET_MONTH)[1])

RUNS_CSV      = f"runs_{YYYYMM}.csv"
LAPS_CSV      = f"runs_{YYYYMM}_laps.csv"
STREAMS_CSV   = f"gps_streams_{YYYYMM}.csv"
ARCHIVE_FILE  = f"{YYYYMM}.html"   # 月別アーカイブ（永続）
OUTPUT        = "index.html"        # 常に当月を index.html にも書く
LAST_FETCH_FILE = os.path.join(".strava_cache", "last_fetch.json")
LAST_COACH_FILE = os.path.join(".strava_cache", "last_coach.json")
COACH_MD = f"coaching_report_{YYYYMM}.md"
GITHUB_REPO = "casa2024takayama/strava-report"
GITHUB_WORKFLOW_URL = f"https://github.com/{GITHUB_REPO}/actions/workflows/update_report.yml"
GITHUB_PAGES_URL = "https://casa2024takayama.github.io/strava-report/"
LOCAL_REPORT_URL = "http://127.0.0.1:8766/index.html"


def detect_report_edition() -> str:
    """HTML 生成時の版（online=GitHub Pages / local=この Mac）。"""
    env = os.environ.get("REPORT_EDITION", "").strip().lower()
    if env in ("online", "local"):
        return env
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return "online"
    return "local"


REPORT_EDITION = detect_report_edition()

# ローカル版のみ埋め込む（online版がGitHub Pagesにトークンを公開しないようガード）
REPORT_SERVER_TOKEN = os.environ.get("REPORT_SERVER_TOKEN", "") if REPORT_EDITION == "local" else ""


def format_last_fetch_label() -> str | None:
    if not os.path.exists(LAST_FETCH_FILE):
        return None
    try:
        with open(LAST_FETCH_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("label"):
            return data["label"]
        return datetime.fromisoformat(data["at"]).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return None


def format_last_coach_meta() -> dict | None:
    if os.path.exists(LAST_COACH_FILE):
        try:
            with open(LAST_COACH_FILE, encoding="utf-8") as f:
                data = json.load(f)
            label = data.get("label")
            if not label and data.get("at"):
                label = datetime.fromisoformat(data["at"]).strftime("%Y-%m-%d %H:%M:%S")
            if label:
                return {
                    "label": label,
                    "model": data.get("model", "Gemini"),
                }
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            pass
    return parse_coach_meta_from_md(COACH_MD)


last_fetch_label = format_last_fetch_label()
if last_fetch_label:
    last_fetch_banner = (
        f'<p class="last-fetch ok" id="last-fetch-msg">'
        f'✓ 最終データ取得: {last_fetch_label}</p>'
    )
else:
    last_fetch_banner = (
        '<p class="last-fetch none" id="last-fetch-msg">'
        'データ未取得 — 「データ更新」で Strava から取得</p>'
    )

last_coach_meta = format_last_coach_meta()
coach_stale = coaching_stale_detail(YYYYMM, RUNS_CSV, LAPS_CSV)
if last_coach_meta:
    coach_class = "last-coach stale" if coach_stale else "last-coach ok"
    last_coach_banner = (
        f'<p class="{coach_class}" id="last-coach-msg">'
        f'✓ 最終 AI 評価: {last_coach_meta["label"]}'
        f'（{html_module.escape(last_coach_meta["model"])}）</p>'
    )
    if coach_stale:
        last_coach_banner += (
            f'<p class="last-coach-warn" id="last-coach-warn">'
            f'⚠️ {html_module.escape(coach_stale["message"])} — '
            f'「AI 評価」または「データ更新」で再生成してください</p>'
        )
else:
    last_coach_banner = (
        '<p class="last-coach none" id="last-coach-msg">'
        'AI 評価未実行 — 「AI 評価」ボタンまたは coach_claude.py で生成</p>'
    )
    if coach_stale:
        last_coach_banner += (
            f'<p class="last-coach-warn" id="last-coach-warn">'
            f'⚠️ {html_module.escape(coach_stale["message"])}</p>'
        )

# Garmin 取得時刻（garmin_daily.csv の更新時刻）
try:
    import garmin as _garmin
    _g_updated = _garmin.last_updated()
except Exception:
    _g_updated = None
if _g_updated:
    last_garmin_banner = (
        f'<p class="last-garmin ok" id="last-garmin-msg">'
        f'✓ Garmin 取得: {_g_updated.strftime("%Y-%m-%d %H:%M:%S")}</p>'
    )
else:
    last_garmin_banner = (
        '<p class="last-garmin none" id="last-garmin-msg">'
        'Garmin 未取得 — garmin_fetch.py を実行</p>'
    )

# ── Garmin ダッシュボード（ローカル版のみ・健康データを公開しない） ──────────
garmin_dashboard_html = ""
garmin_chart_js = ""
if REPORT_EDITION == "local":
    try:
        import garmin as _gm
        _g_recent = _gm.recent_daily(14, TARGET_YEAR, TARGET_MONTH)
        _g_series = _gm.monthly_series(TARGET_YEAR, TARGET_MONTH)

        if _g_recent:
            def _gc(r, k):
                x = (r.get(k) or "").strip()
                return x if x else "—"
            _rows = "".join(
                f"<tr><td>{r.get('date','')[5:]}</td>"
                f"<td>{_gc(r,'vo2max')}</td><td>{_gc(r,'readiness_score')}</td>"
                f"<td>{_gc(r,'hrv_last_night')}</td><td>{_gc(r,'sleep_score')}</td>"
                f"<td>{_gc(r,'sleep_hours')}</td><td>{_gc(r,'resting_hr')}</td>"
                f"<td>{_gc(r,'training_status')}</td></tr>"
                for r in _g_recent
            )
            garmin_dashboard_html = f"""
  <div class="section garmin-dash">
    <h2>⌚ Garmin 指標ダッシュボード <small>（ローカル版のみ・直近{len(_g_recent)}日）</small></h2>
    <canvas id="garminChart" height="140"></canvas>
    <div class="table-wrap">
    <table class="garmin-table">
      <thead><tr><th>日付</th><th>VO2max</th><th>レディネス</th><th>夜間HRV</th>
        <th>睡眠スコア</th><th>睡眠h</th><th>安静時HR</th><th>ステータス</th></tr></thead>
      <tbody>{_rows}</tbody>
    </table>
    </div>
  </div>"""

        if _g_series and _g_series.get("dates"):
            _specs = [
                ("vo2max", "VO2max", "#dc2626", "yv"),
                ("readiness_score", "レディネス", "#6366f1", "y"),
                ("hrv_last_night", "夜間HRV(ms)", "#16a34a", "y"),
                ("sleep_score", "睡眠スコア", "#f59e0b", "y"),
            ]
            _datasets = []
            for key, label, color, axis in _specs:
                if key in _g_series:
                    _datasets.append(
                        "{label:%s,data:%s,borderColor:'%s',backgroundColor:'%s22',"
                        "yAxisID:'%s',tension:.3,spanGaps:true,pointRadius:2,borderWidth:2}"
                        % (json.dumps(label, ensure_ascii=False), json.dumps(_g_series[key]),
                           color, color, axis)
                    )
            garmin_chart_js = (
                "new Chart(document.getElementById('garminChart'), {type:'line',data:{labels:%s,datasets:[%s]},"
                "options:{responsive:true,interaction:{mode:'index',intersect:false},"
                "plugins:{legend:{labels:{font:{size:11}}}},"
                "scales:{y:{position:'left',title:{display:true,text:'レディネス/HRV/睡眠'},min:0},"
                "yv:{position:'right',title:{display:true,text:'VO2max'},grid:{drawOnChartArea:false}}}}});"
                % (json.dumps(_g_series["dates"], ensure_ascii=False), ",".join(_datasets))
            )
    except Exception as _e:
        garmin_dashboard_html = ""
        garmin_chart_js = ""

# Garmin 取得ボタン（ローカル版のみ）
garmin_btn_html = (
    '<button type="button" class="btn-garmin" id="btn-garmin">⌚ Garmin取得</button>'
    if REPORT_EDITION == "local" else ""
)

# ── 月別ナビゲーション ─────────────────────────────────────────────────────
def _available_months():
    """ディレクトリ内の月別HTMLファイルを新しい順で返す"""
    pat = re.compile(r'^(20\d{2})(\d{2})\.html$')
    months = []
    for f in glob.glob("20*.html"):
        m = pat.match(f)
        if m:
            y, mo = int(m.group(1)), int(m.group(2))
            months.append((y, mo, f))
    months.sort(reverse=True)
    return months

def build_month_nav():
    months = _available_months()
    current = (TARGET_YEAR, TARGET_MONTH, ARCHIVE_FILE)
    if current not in months:
        months.append(current)

    months_asc = sorted(months)   # 古い順
    if len(months_asc) <= 1:
        return ""

    try:
        idx = next(i for i, (y, mo, _) in enumerate(months_asc)
                   if y == TARGET_YEAR and mo == TARGET_MONTH)
    except StopIteration:
        return ""

    is_latest = (idx == len(months_asc) - 1)

    # ← 前（古い）月
    if idx > 0:
        py, pm, pf = months_asc[idx - 1]
        prev_html = f'<a href="{pf}" class="mnav-arrow">← {pm}月</a>'
    else:
        prev_html = '<span class="mnav-arrow mnav-disabled">←</span>'

    # → 次（新しい）月
    if not is_latest:
        ny, nm, nf = months_asc[idx + 1]
        next_label = "最新 →" if (idx + 1 == len(months_asc) - 1) else f"{nm}月 →"
        next_html  = f'<a href="{nf}" class="mnav-arrow">{next_label}</a>'
    else:
        next_html = '<span class="mnav-arrow mnav-disabled">→</span>'

    cur_label = f"{TARGET_YEAR}年{TARGET_MONTH}月{'　✦ 最新' if is_latest else ''}"

    # アーカイブ閲覧中のみ「最新へ」固定ボタン
    back_btn = ('' if is_latest else
                '<a href="index.html" class="back-latest">↑ 最新データへ</a>')

    return f"""<nav class="month-nav">
  {prev_html}
  <span class="mnav-label">{cur_label}</span>
  {next_html}
</nav>
{back_btn}"""

# ── アスリートプロフィール（表示には使用しない） ───────────────────────────
_WEIGHT_KG    = 65
_HEIGHT_CM    = 174
_MONTH_GOAL   = 200   # km/月

# ── レーススケジュール（races.json から自動読み込み） ────────────────────────
_RACES_FILE = "races.json"
def _load_races():
    if os.path.exists(_RACES_FILE):
        with open(_RACES_FILE) as f:
            data = json.load(f)
        return [(date.fromisoformat(r["date"]), r["name"], float(r["dist_km"]))
                for r in data]
    return []

_RACES     = _load_races()
_today_ref = date.today()
_next_races = [(d, n, dist) for d, n, dist in _RACES if d >= _today_ref]
_NEXT_RACE  = _next_races[0][0]  if _next_races else None
_RACE_NAME  = _next_races[0][1]  if _next_races else None
_RACE_DIST  = _next_races[0][2]  if _next_races else 42.195

# 月間200kmドロップダウン用：今月以降の次レース + 定番オプション
_month_next = [(d, n, dist) for d, n, dist in _next_races
               if d.year == TARGET_YEAR and d.month == TARGET_MONTH]
_race_select_opts = ""
for _d, _n, _dist in _month_next:
    _race_select_opts += f'<option value="{_dist}">{_n} +{_dist:.1f}km</option>\n              '
if not any(abs(_dist - 42.195) < 0.5 for _, _, _dist in _month_next):
    _race_select_opts += '<option value="42.2">フルマラソン ＋42.2km</option>\n              '
if not any(abs(_dist - 21.0975) < 0.5 for _, _, _dist in _month_next):
    _race_select_opts += '<option value="21.1">ハーフマラソン ＋21.1km</option>\n              '
_race_select_opts += '<option value="0">レースなし（走行距離のみ）</option>'
try:
    import garmin as _garmin
    _VO2MAX   = int(round(_garmin.latest_vo2max() or 59))  # Garmin 実測 VO2Max を自動反映
except Exception:
    _VO2MAX   = 59
# Garmin 計測 VO2Max（実測があれば上で反映。HTML上でスライダー変更可能）
_GOAL_1_SEC   = 3*3600 + 10*60   # Sub 3:10
_GOAL_ULT_SEC = 3*3600            # Sub 3:00
# VDOT 59 ダニエルズ基準ペース（秒/km）※スライダーで動的変更
_E_LO, _E_HI  = 291, 312   # 4:51-5:12/km  Easy
_M_PACE       = 264         # 4:24/km       Marathon
_T_LO, _T_HI  = 244, 251   # 4:04-4:11/km  Threshold
_I_PACE       = 221         # 3:41/km       Interval
_R_PACE       = 207         # 3:27/km       Repetition

# 現在の練習プラン基準ペース（VDOT 51 / 5km PB 19:37 実走力ベース）
_TRAIN_I_LO = 228   # 3:48/km  インターバル設定ペース（速め限界）
_TRAIN_I_HI = 234   # 3:54/km  インターバル設定ペース（遅め限界）

# ── PB 読み込み ────────────────────────────────────────────────────────────
_PBS_FILE = "pbs.json"
_PB_META = {
    "1mile": {"label": "1 Mile", "dist_km": 1.609},
    "3km":   {"label": "3 km",   "dist_km": 3.0},
    "5km":   {"label": "5 km",   "dist_km": 5.0},
    "10km":  {"label": "10 km",  "dist_km": 10.0},
    "half":  {"label": "Half",   "dist_km": 21.0975},
    "full":  {"label": "Full",   "dist_km": 42.195},
}
# Riegel式: T2 = T1 × (D2/D1)^1.06
def _riegel(t1_sec, d1_km, d2_km):
    return int(t1_sec * (d2_km / d1_km) ** 1.06)

def _sec_to_str(sec):
    h, r = divmod(int(sec), 3600); m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def load_pbs():
    if os.path.exists(_PBS_FILE):
        with open(_PBS_FILE) as f:
            return json.load(f)
    return {}

# Sub 3:10 / Sub 3:00 の各距離換算タイム（秒）
_FULL_310 = 3*3600 + 10*60  # 11400
_FULL_300 = 3*3600           # 10800
_TARGETS = {}
for _k, _m in _PB_META.items():
    _TARGETS[_k] = {
        "sub310": _riegel(_FULL_310, 42.195, _m["dist_km"]),
        "sub300": _riegel(_FULL_300, 42.195, _m["dist_km"]),
    }

# フルマラソンPBは pbs.json から自動取得
_pbs_raw = load_pbs()
_CURRENT_PB_SEC = _pbs_raw.get("full", {}).get("time_sec", 3*3600+17*60+1)
_CURRENT_PB     = _sec_to_str(_CURRENT_PB_SEC)

# ── ユーティリティ ─────────────────────────────────────────────────────────
def parse_time_sec(t):
    if not t: return 0
    parts = list(map(int, str(t).split(":")))
    if len(parts) == 3: return parts[0]*3600 + parts[1]*60 + parts[2]
    if len(parts) == 2: return parts[0]*60 + parts[1]
    return int(parts[0])

def fmt_time(sec):
    h, r = divmod(int(sec), 3600); m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def pace_to_sec(p):
    if not p: return None
    try: m, s = p.split(":"); return int(m)*60+int(s)
    except: return None

def week_label(date_str):
    try:
        d = date.fromisoformat(date_str)
        w = (d - MONTH_START).days // 7 + 1
        mon = MONTH_START + timedelta(weeks=w-1)
        sun = mon + timedelta(days=6)
        return f"第{w}週 ({mon.strftime('%-m/%-d')}〜{min(sun, MONTH_END).strftime('%-m/%-d')})"
    except: return "?"

def training_type(run):
    pace = pace_to_sec(run.get("pace_per_km"))
    hr   = float(run.get("avg_heartrate") or 0)
    dist = float(run.get("distance_km") or 0)
    if dist >= 20: return ("ロング走", "#6366f1")
    if hr >= 165:  return ("インターバル", "#ef4444")
    if hr >= 155 or (pace and pace <= 270): return ("テンポ走", "#f59e0b")
    return ("イージー走", "#22c55e")

def load_csv(path):
    if not os.path.exists(path): return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def load_gps():
    """GPS ストリームを activity_id ごとに辞書へ（間引き済み）"""
    rows = load_csv(STREAMS_CSV)
    by_id = defaultdict(list)
    for r in rows:
        by_id[r["activity_id"]].append(r)
    # 地図描画用に間引き（最大 500 点）
    result = {}
    for aid, pts in by_id.items():
        step = max(1, len(pts) // 500)
        result[aid] = [
            [float(p["lat"]), float(p["lng"])]
            for p in pts[::step]
            if p.get("lat") and p.get("lng")
        ]
    return result

# ── PB 階段セクション ──────────────────────────────────────────────────────
def build_pb_ladder(pbs):
    keys = ["1mile", "3km", "5km", "10km", "half", "full"]
    cards = ""
    for k in keys:
        meta   = _PB_META[k]
        pb     = pbs.get(k, {})
        t310   = _TARGETS[k]["sub310"]
        t300   = _TARGETS[k]["sub300"]
        pb_sec = pb.get("time_sec")
        pb_str = pb.get("time_str", "—")
        pb_date= pb.get("date", "")
        manual = pb.get("source") == "manual"

        # 達成状況
        if pb_sec:
            reach310 = pb_sec <= t310
            reach300 = pb_sec <= t300
            # バー: 0% = 現在PBと同じ、100% = Sub3:00達成
            # 範囲を t310〜t300 で可視化（t310が左端）
            lo, hi = t300, t310   # 小さいほど良い（逆転）
            bar_pct = max(0, min(100, int((t310 - pb_sec) / max(t310 - t300, 1) * 100))) if pb_sec else 0
            if reach300:
                status_color, status_txt = "#22c55e", "✅ Sub 3:00"
            elif reach310:
                status_color, status_txt = "#f59e0b", "✅ Sub 3:10"
            else:
                gap = pb_sec - t310
                m, s = divmod(gap, 60)
                status_color, status_txt = "#94a3b8", f"Sub 3:10 まで -{m}:{s:02d}"
        else:
            bar_pct, status_color, status_txt = 0, "#e2e8f0", "未計測"

        manual_badge = '<span style="font-size:10px;color:#a0aec0;margin-left:4px">手入力</span>' if manual else ""

        cards += f"""
        <div class="pb-card">
          <div class="pb-dist">{meta['label']}</div>
          <div class="pb-time">{pb_str}{manual_badge}</div>
          <div class="pb-date">{pb_date}</div>
          <div class="pb-bar-bg">
            <div class="pb-bar-fg" style="width:{bar_pct}%;background:{status_color}"></div>
          </div>
          <div class="pb-targets">
            <span style="color:#f59e0b;font-size:10px">{_sec_to_str(t310)}</span>
            <span style="color:{status_color};font-size:11px;font-weight:700">{status_txt}</span>
            <span style="color:#22c55e;font-size:10px">{_sec_to_str(t300)}</span>
          </div>
        </div>"""

    # 「最も伸びしろがある距離」を特定
    gaps = {}
    for k in keys:
        pb_sec = pbs.get(k, {}).get("time_sec")
        if pb_sec:
            t310 = _TARGETS[k]["sub310"]
            gaps[k] = pb_sec - t310  # マイナスなら達成済み

    if gaps:
        worst = max(gaps, key=lambda k: gaps[k])
        if gaps[worst] > 0:
            m, s = divmod(gaps[worst], 60)
            next_target = f"<div style='font-size:12px;color:#4a5568;margin-top:12px'>📌 次の重点距離：<strong>{_PB_META[worst]['label']}</strong>（Sub 3:10 まで あと {m}:{s:02d}）</div>"
        else:
            next_target = "<div style='font-size:12px;color:#22c55e;margin-top:12px'>🎉 全距離で Sub 3:10 達成！Sub 3:00 に挑戦！</div>"
    else:
        next_target = ""

    return f"""
    <div class="plan-box" style="margin-bottom:24px">
      <div class="plan-label">🏅 PB 階段（距離別自己ベスト）</div>
      <div style="font-size:11px;color:#a0aec0;margin-bottom:12px">
        バーは Sub 3:10〜Sub 3:00 相当タイムの範囲を示します。Strava の best_efforts から自動更新。
      </div>
      <div class="pb-ladder">{cards}</div>
      {next_target}
    </div>"""

# ── パフォーマンスプロフィール & Sub-3 ロードマップ ───────────────────────
def build_performance_profile():
    """VO2Max・VDOT・目標タイム・練習ペース・Sub-3ロードマップ HTML"""

    # VDOT をフルマラソンPBから推定（Daniels 近似式）
    import math as _math
    def _sec_to_vdot(sec):
        v   = 42195 / sec * 60  # m/min
        vo2 = -4.6 + 0.182258*v + 0.000104*v**2
        t   = sec / 60
        pct = 0.8 + 0.1894393*_math.exp(-0.012778*t) + 0.2989558*_math.exp(-0.1932605*t)
        return round(vo2 / pct, 1)
    vdot_pb  = _sec_to_vdot(_CURRENT_PB_SEC)  # 3:17:01 → VDOT ≈ 54
    vdot_pb_int = round(vdot_pb)
    vdot_vo2 = _VO2MAX
    vdot_gap = round(vdot_vo2 - vdot_pb, 1)

    return f"""
    <div class="perf-section">

      <!-- VO2Max スライダー＋潜在走力 -->
      <div class="perf-grid">
        <div class="perf-box">
          <div class="plan-label">🧬 VO₂Max & 潜在走力</div>
          <div style="display:flex;align-items:center;gap:16px;margin-top:10px;flex-wrap:wrap">
            <!-- 数値表示 -->
            <div style="text-align:center;min-width:80px">
              <div style="font-size:11px;color:#a0aec0;margin-bottom:2px">Garmin VO₂Max</div>
              <div id="vo2-display" style="font-size:48px;font-weight:900;color:#3b82f6;line-height:1">{vdot_vo2}</div>
              <div style="font-size:11px;color:#a0aec0">ml/kg/min</div>
            </div>
            <!-- スライダー -->
            <div style="flex:1;min-width:180px">
              <input type="range" id="vo2-slider" min="45" max="75" value="{vdot_vo2}"
                oninput="updateVdot(this.value)"
                style="width:100%;accent-color:#3b82f6;cursor:pointer;margin-bottom:6px">
              <div style="display:flex;justify-content:space-between;font-size:10px;color:#cbd5e0">
                <span>45</span><span>55</span><span>65</span><span>75</span>
              </div>
              <div id="vo2-desc" style="font-size:12px;color:#4a5568;line-height:1.7;margin-top:6px"></div>
            </div>
          </div>
        </div>

        <div class="perf-box" style="text-align:center">
          <div class="plan-label">🎯 目標タイム</div>
          <div style="margin-top:8px">
            <div style="font-size:11px;color:#a0aec0">NEXT</div>
            <div style="font-size:26px;font-weight:800;color:#f59e0b">Sub 3:10</div>
            <div id="g1-months" style="font-size:11px;color:#a0aec0;margin-bottom:10px"></div>
            <div style="font-size:11px;color:#a0aec0">ULTIMATE</div>
            <div style="font-size:26px;font-weight:800;color:#ef4444">Sub 3:00</div>
            <div id="ult-months" style="font-size:11px;color:#a0aec0"></div>
          </div>
        </div>
      </div>

      <!-- VDOT 進捗バー (JS 制御) -->
      <div class="perf-box" style="margin-top:0">
        <div class="plan-label">📊 VDOT 進捗（自己ベスト → Sub-3 まで）</div>
        <div style="position:relative;height:26px;background:#e2e8f0;border-radius:13px;margin:16px 0 8px">
          <div style="position:absolute;left:0;top:0;height:26px;width:100%;border-radius:13px;
               background:linear-gradient(90deg,#94a3b8 0%,#3b82f6 64%,#f59e0b 64%,#ef4444 100%)"></div>
          <div id="vdot-marker" style="position:absolute;top:-5px;transform:translateX(-50%);
               background:#fff;border:3px solid #3b82f6;border-radius:50%;
               width:24px;height:24px;z-index:2;transition:left .3s"></div>
          <div id="vdot-label" style="position:absolute;top:30px;transform:translateX(-50%);
               font-size:10px;color:#3b82f6;font-weight:700;white-space:nowrap;transition:left .3s"></div>
          <div style="position:absolute;left:0;top:30px;font-size:10px;color:#718096">VDOT {vdot_pb_int}<br>（PB {_CURRENT_PB}）</div>
          <div style="position:absolute;left:64%;top:30px;transform:translateX(-50%);
               font-size:10px;color:#f59e0b;font-weight:700;white-space:nowrap">VDOT 59<br>Sub 3:10</div>
          <div style="position:absolute;right:0;top:30px;text-align:right;
               font-size:10px;color:#ef4444;font-weight:700">VDOT 63<br>Sub 3:00</div>
        </div>
        <div style="height:40px"></div>
      </div>

      <!-- 練習ペース表（JS 更新） -->
      <div class="perf-pace-grid">
        <div class="perf-box">
          <div class="plan-label">🏃 現在の練習ペース（VO₂Max <span id="pace-vo2-label">{vdot_vo2}</span> 基準）</div>
          <table style="width:100%;border-collapse:collapse;margin-top:8px" id="pace-table">
            <tbody>
              <tr><td><span class="pace-badge" style="background:#22c55e">E イージー</span></td>
                  <td id="p-e" style="font-weight:700;font-size:14px"></td>
                  <td style="font-size:12px;color:#718096">有酸素基礎・回復</td></tr>
              <tr><td><span class="pace-badge" style="background:#3b82f6">M マラソン</span></td>
                  <td id="p-m" style="font-weight:700;font-size:14px"></td>
                  <td style="font-size:12px;color:#718096">レースペース目標</td></tr>
              <tr><td><span class="pace-badge" style="background:#f59e0b">T テンポ</span></td>
                  <td id="p-t" style="font-weight:700;font-size:14px"></td>
                  <td style="font-size:12px;color:#718096">乳酸閾値向上</td></tr>
              <tr><td><span class="pace-badge" style="background:#ef4444">I インターバル</span></td>
                  <td id="p-i" style="font-weight:700;font-size:14px"></td>
                  <td style="font-size:12px;color:#718096">VO₂max 向上</td></tr>
              <tr><td><span class="pace-badge" style="background:#8b5cf6">R レペティション</span></td>
                  <td id="p-r" style="font-weight:700;font-size:14px"></td>
                  <td style="font-size:12px;color:#718096">スピード・走力</td></tr>
            </tbody>
          </table>
        </div>

        <div class="perf-box">
          <div class="plan-label">🏆 Sub-3:00 目標ペース（VDOT 63）</div>
          <table style="width:100%;border-collapse:collapse;margin-top:8px">
            <tr>
              <td style="text-align:center"><div style="font-size:11px;color:#22c55e;font-weight:700">E</div><div style="font-size:12px">4:41〜5:02</div></td>
              <td style="text-align:center"><div style="font-size:11px;color:#3b82f6;font-weight:700">M</div><div style="font-size:12px">4:15/km</div></td>
              <td style="text-align:center"><div style="font-size:11px;color:#f59e0b;font-weight:700">T</div><div style="font-size:12px">3:57/km</div></td>
              <td style="text-align:center"><div style="font-size:11px;color:#ef4444;font-weight:700">I</div><div style="font-size:12px">3:33/km</div></td>
              <td style="text-align:center"><div style="font-size:11px;color:#8b5cf6;font-weight:700">R</div><div style="font-size:12px">3:19/km</div></td>
            </tr>
          </table>
          <div style="margin-top:14px;font-size:12px;color:#4a5568;font-weight:700;margin-bottom:6px">Sub-3:00 への 3 つの柱</div>
          <div style="font-size:12px;color:#718096;line-height:1.9">
            <span style="color:#ef4444;font-weight:700">①</span> 週間走行量 <strong>70〜80km</strong>（現在 ~50km）<br>
            <span style="color:#f59e0b;font-weight:700">②</span> 月2回以上の <strong>30km ロング走</strong><br>
            <span style="color:#22c55e;font-weight:700">③</span> 週1回の <strong>テンポ走</strong> or <strong>インターバル</strong>
          </div>
          <div style="margin-top:10px;padding:10px;background:#fef3c7;border-radius:8px;font-size:11px;color:#92400e;line-height:1.7">
            💡 <strong>Pfitzinger</strong>: 週80〜90km・ミディアムロング走週2回<br>
            <strong>Hansons</strong>: 累積疲労活用・ロング上限26km
          </div>
        </div>
      </div>

      <!-- VO2Max JS ロジック -->
      <script>
      (function(){{
        // VDOT → [eLo, eHi, marathon, tempo, interval, rep] (秒/km)
        const T = {{
          45:[349,374,310,300,269,254], 47:[339,363,301,290,261,246],
          49:[329,353,293,282,253,238], 50:[324,347,289,278,249,234],
          51:[319,342,286,274,246,231], 52:[314,337,282,269,242,228],
          53:[309,332,278,265,238,224], 54:[304,326,274,261,234,220],
          55:[300,322,271,257,231,217], 56:[296,317,267,254,227,213],
          57:[293,314,264,250,224,210], 58:[290,311,261,247,222,208],
          59:[287,308,258,244,219,205], 60:[284,305,255,241,216,202],
          61:[281,302,252,238,213,199], 62:[278,299,249,235,210,197],
          63:[275,296,246,232,208,194], 64:[272,293,243,230,205,192],
          65:[270,290,241,227,203,189], 67:[264,284,236,222,198,185],
          70:[257,276,229,216,192,179],
        }};

        function interp(v) {{
          const keys = Object.keys(T).map(Number).sort((a,b)=>a-b);
          for (let i=0; i<keys.length-1; i++) {{
            const k1=keys[i], k2=keys[i+1];
            if (v>=k1 && v<=k2) {{
              const r=(v-k1)/(k2-k1);
              return T[k1].map((a,j)=>Math.round(a+(T[k2][j]-a)*r));
            }}
          }}
          return v<=keys[0]?T[keys[0]]:T[keys[keys.length-1]];
        }}

        function sec2pace(s) {{
          return Math.floor(s/60)+':'+(s%60<10?'0':'')+s%60;
        }}

        function vdotToMarathon(v) {{
          // s = linear fit between anchors
          const anchors = [[45,13500],[50,12480],[52,12060],[55,11580],[58,11280],
                           [59,11160],[60,11040],[63,10740],[65,10500],[70,9960]];
          for (let i=0;i<anchors.length-1;i++) {{
            const [v1,s1]=anchors[i],[v2,s2]=anchors[i+1];
            if (v>=v1&&v<=v2) {{ return Math.round(s1+(s2-s1)*(v-v1)/(v2-v1)); }}
          }}
          return v<anchors[0][0]?anchors[0][1]:anchors[anchors.length-1][1];
        }}

        function fmtTime(s) {{
          const h=Math.floor(s/3600),m=Math.floor((s%3600)/60);
          return h+':'+(m<10?'0':'')+m;
        }}

        window.updateVdot = function(v) {{
          v = parseInt(v);
          const p = interp(v);
          const VDOT_PB={vdot_pb_int}, VDOT_G1=59, VDOT_ULT=63;

          // 表示更新
          document.getElementById('vo2-display').textContent = v;
          document.getElementById('pace-vo2-label').textContent = v;
          document.getElementById('vo2-slider').value = v;

          // ペース表
          document.getElementById('p-e').textContent = sec2pace(p[0])+'〜'+sec2pace(p[1])+'/km';
          document.getElementById('p-m').textContent = sec2pace(p[2])+'/km';
          document.getElementById('p-t').textContent = sec2pace(p[3])+'〜'+sec2pace(p[3]+6)+'/km';
          document.getElementById('p-i').textContent = sec2pace(p[4])+'/km';
          document.getElementById('p-r').textContent = sec2pace(p[5])+'/km';

          // 説明文
          const mSec = vdotToMarathon(v);
          const gap = v - VDOT_PB;
          document.getElementById('vo2-desc').innerHTML =
            'VO₂Max '+v+' は <strong>'+fmtTime(mSec)+'相当</strong>の生理的能力。<br>'+
            'PB との差 <strong style="color:#f59e0b">'+gap+' VDOT</strong> が潜在力です。';

          // 目標月数
          const toG1 = Math.max(0, VDOT_G1-v);
          const toUlt = Math.max(0, VDOT_ULT-v);
          document.getElementById('g1-months').textContent =
            toG1<=0 ? '✅ 到達済み' : '目安 '+(Math.round(toG1/1.5*3))+'〜'+(Math.round(toG1/1.0*3))+' ヶ月';
          document.getElementById('ult-months').textContent =
            toUlt<=0 ? '✅ 到達済み' : '目安 '+(Math.round(toUlt/1.5*3))+'〜'+(Math.round(toUlt/1.0*3))+' ヶ月';

          // VDOT バー（PB=0%, ULT=100%, range=63-52=11）
          const range = VDOT_ULT - VDOT_PB;
          const pct = Math.min(100, Math.max(0, (v-VDOT_PB)/range*100));
          document.getElementById('vdot-marker').style.left = pct+'%';
          document.getElementById('vdot-label').style.left  = pct+'%';
          document.getElementById('vdot-label').innerHTML = 'VDOT '+v+'<br>（現在）';
        }};

        // 初期描画
        updateVdot({vdot_vo2});
      }})();
      </script>

    </div>"""


# ── トップサマリー：レース準備＋練習メニュー ──────────────────────────────
def build_top_plan(runs):
    today       = date.today()
    current_km  = sum(float(r["distance_km"] or 0) for r in runs)
    remaining   = max(0, _MONTH_GOAL - current_km)

    # 週別距離（傾向分析用）
    by_week = defaultdict(float)
    for r in runs:
        try:
            d = date.fromisoformat(r["date"])
            w = (d - MONTH_START).days // 7 + 1
            by_week[w] += float(r["distance_km"] or 0)
        except: pass
    week_vols = [by_week.get(w, 0) for w in sorted(by_week)]

    # 傾向コメント
    if len(week_vols) >= 3:
        trend_val = week_vols[-1] - week_vols[1]
        if trend_val > 10:
            trend_txt = "距離・負荷が順調に増加しており、<strong>フィットネスが回復・向上中</strong>です。"
        elif trend_val > 0:
            trend_txt = f"{MONTH_LABEL}の練習から着実に走行量を積み上げています。"
        else:
            trend_txt = "テーパー週に入っており、走行量を適切に落とせています。"
    else:
        trend_txt = "練習を継続しています。"

    # 月間200km進捗バー（実績のみ Python 計算、見込みは JS）
    pct = min(100, round(current_km / _MONTH_GOAL * 100, 1))

    # レース週練習メニュー（次のレースが今月かつ未来の場合のみ表示）
    days_to_race  = (_NEXT_RACE - today).days if _NEXT_RACE else None
    race_week_plan = []
    if _NEXT_RACE and 0 <= days_to_race <= 14:
        plan_map = {
            0: ("今日",  "🟢 イージー 8km（E ペース）筋肉を起こす程度"),
            1: ("明日",  "🟢 イージー 6km または完全休養"),
            2: ("+2日",  "🟢 イージー 8km＋最後にストライド 4本×100m"),
            3: ("+3日",  "🟢 イージー 5km（軽め）"),
            4: ("+4日",  "😴 完全休養 or ジョグ 3km のみ"),
            5: ("+5日",  "😴 完全休養（レース2日前）"),
            6: ("+6日",  "🔴 ジョグ 3km＋ストライド 2本（前日：脚を動かすだけ）"),
            7: ("+7日",  f"🏆 <strong>{_RACE_NAME}</strong>"),
        }
        for delta in range(8):
            target_date = today + timedelta(days=delta)
            if target_date > _NEXT_RACE: break
            label, menu = plan_map.get(delta, ("", ""))
            day_str = target_date.strftime("%-m/%-d") + f"（{['月','火','水','木','金','土','日'][target_date.weekday()]}）"
            is_race = (target_date == _NEXT_RACE)
            race_week_plan.append((day_str, label, menu, is_race))

    # HTML 組み立て
    plan_rows = ""
    for day_str, label, menu, is_race in race_week_plan:
        bg = "background:#fff5f5;font-weight:600" if is_race else ""
        plan_rows += f"""<tr style="{bg}">
          <td class="rw-col-date" style="white-space:nowrap;color:#718096;font-size:12px">{day_str}</td>
          <td class="rw-col-label" style="font-size:11px;color:#a0aec0;white-space:nowrap">{label}</td>
          <td class="rw-col-menu" style="font-size:13px">{menu}</td>
        </tr>"""

    return f"""
    <div class="top-plan">

      <!-- 傾向 & レースカウントダウン -->
      <div class="top-plan-grid">
        <div class="plan-box trend-box">
          <div class="plan-label">📈 {MONTH_LABEL}の練習傾向</div>
          <p style="font-size:13px;line-height:1.8;color:#4a5568">{trend_txt}
            週間距離が {'→'.join(f'<strong>{v:.0f}km</strong>' for v in week_vols)} と推移。
          </p>
        </div>
        {'<div class="plan-box race-box"><div class="plan-label">🗓 次のレース</div>' +
         f'<div class="race-name">{_RACE_NAME}</div>' +
         f'<div class="race-date">{_NEXT_RACE.strftime("%-m月%-d日")}</div>' +
         f'<div class="race-days">あと <span>{days_to_race}</span> 日</div></div>'
         if _NEXT_RACE and days_to_race is not None and days_to_race >= 0
         else '<div class="plan-box" style="text-align:center;color:#a0aec0;font-size:13px;display:flex;align-items:center;justify-content:center">次のレースは未登録</div>'}
      </div>

      <!-- 月間200km進捗（JS インタラクティブ） -->
      <div class="plan-box" style="margin-bottom:20px">
        <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;margin-bottom:12px">
          <div class="plan-label" style="margin-bottom:0">🎯 月間 {_MONTH_GOAL}km 目標</div>
          <!-- ドロップダウン：予定レース選択 -->
          <div style="display:flex;align-items:center;gap:8px">
            <span style="font-size:11px;color:#718096;white-space:nowrap">予定レース</span>
            <select id="race-select" onchange="updateGoalBar()"
              style="font-size:12px;padding:4px 8px;border:1px solid #e2e8f0;
                     border-radius:8px;background:#fff;color:#4a5568;cursor:pointer">
              {_race_select_opts}
            </select>
          </div>
        </div>

        <!-- テキスト行 -->
        <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:10px">
          <span style="font-size:13px;color:#4a5568">
            走行実績 <strong>{current_km:.1f}km</strong>
            <span style="font-size:11px;color:#a0aec0;margin-left:4px">（レース含む記録済み分）</span>
          </span>
          <span style="font-size:13px;color:#a0aec0">＋</span>
          <span style="font-size:13px;color:#718096" id="proj-add-label">予定レース <strong>42.2km</strong></span>
          <span style="font-size:13px;color:#a0aec0">=</span>
          <span style="font-size:13px;font-weight:700" id="proj-total-label">見込み 203.0km</span>
          <span style="font-size:13px" id="proj-status-label">✅ 達成見込み</span>
        </div>

        <!-- プログレスバー -->
        <div style="height:12px;background:#e2e8f0;border-radius:6px;position:relative;overflow:hidden">
          <div id="actual-bar"
               style="height:12px;background:#94a3b8;border-radius:6px;
                      width:{pct}%;transition:width .4s"></div>
          <div id="proj-bar"
               style="height:12px;background:#f59e0b;border-radius:0 6px 6px 0;
                      position:absolute;top:0;left:{pct}%;
                      width:0%;opacity:0.55;transition:all .4s"></div>
        </div>
        <div style="display:flex;justify-content:space-between;font-size:11px;color:#a0aec0;margin-top:5px">
          <span>0km</span>
          <span style="color:#718096">{pct:.0f}% 走行済み</span>
          <span id="proj-pct-label" style="color:#f59e0b"></span>
          <span>{_MONTH_GOAL}km</span>
        </div>

        <!-- JS -->
        <script>
        (function() {{
          var CURRENT = {current_km:.1f};
          var GOAL    = {_MONTH_GOAL};
          var ACT_PCT = {pct:.1f};

          function updateGoalBar() {{
            var add     = parseFloat(document.getElementById('race-select').value);
            var proj    = CURRENT + add;
            var projPct = Math.min(100, proj / GOAL * 100);
            var addPct  = Math.max(0, projPct - ACT_PCT);
            var reached = proj >= GOAL;
            var color   = reached ? '#22c55e' : '#f59e0b';

            document.getElementById('proj-bar').style.left       = ACT_PCT + '%';
            document.getElementById('proj-bar').style.width      = addPct + '%';
            document.getElementById('proj-bar').style.background = color;

            // テキスト更新
            if (add > 0) {{
              document.getElementById('proj-add-label').innerHTML =
                '予定レース <strong>' + add.toFixed(1) + 'km</strong>';
              document.getElementById('proj-add-label').style.display = '';
              document.getElementById('proj-total-label').innerHTML =
                '<span style="color:' + color + '">見込み <strong>' + proj.toFixed(1) + 'km</strong></span>';
              document.getElementById('proj-pct-label').style.color = color;
              document.getElementById('proj-pct-label').textContent =
                projPct.toFixed(0) + '%（レース後）';
            }} else {{
              document.getElementById('proj-add-label').style.display = 'none';
              document.getElementById('proj-total-label').innerHTML =
                '<span style="color:' + color + '">現状 <strong>' + CURRENT.toFixed(1) + 'km</strong></span>';
              document.getElementById('proj-pct-label').textContent = '';
            }}

            document.getElementById('proj-status-label').innerHTML =
              reached ? '✅ <span style="color:#22c55e">達成見込み</span>'
                      : '<span style="color:#f59e0b">あと ' + (GOAL - proj).toFixed(1) + 'km</span>';
          }}

          // グローバルに公開
          window.updateGoalBar = updateGoalBar;
          updateGoalBar();  // 初期描画
        }})();
        </script>
      </div>

      {f'''<!-- レース週練習メニュー -->
      <div class="plan-box">
        <div class="plan-label">📋 レース週 推奨練習メニュー（ダニエルズ式テーパー）</div>
        <table style="width:100%;border-collapse:collapse;margin-top:10px">
          <thead>
            <tr style="background:#f7fafc">
              <th style="text-align:left;padding:8px 10px;font-size:11px;color:#718096;border-bottom:2px solid #e2e8f0">日付</th>
              <th style="text-align:left;padding:8px 10px;font-size:11px;color:#718096;border-bottom:2px solid #e2e8f0"></th>
              <th style="text-align:left;padding:8px 10px;font-size:11px;color:#718096;border-bottom:2px solid #e2e8f0">メニュー</th>
            </tr>
          </thead>
          <tbody>{plan_rows}</tbody>
        </table>
        <p style="font-size:11px;color:#a0aec0;margin-top:10px">
          ※ テーパー期の原則：スピードは落とさずに量を減らす。最終3日はなるべく安静に。
        </p>
      </div>''' if race_week_plan else ''}

    </div>"""


# ── 週次練習メニュー（固定テンプレート対実績評価） ────────────────────────────
# 月曜始まり・VO2Max 59 基準・週40〜44km目標プラン
_WEEKLY_PLAN = [
    # (weekday 0=Mon, type_key, label, target_km, description)
    # 目標: 週45〜50km・5000mスピード強化フェーズ（VDOT 51 / 5km PB 19:37 基準）
    (0, "rest",     "休養",           0,  "完全休養"),
    (1, "tempo",    "中強度+スピード", 7,  "持久走 6km（4:20〜4:40/km）＋ 200m×5本（44〜47秒/本・間は200mジョグ）"),
    (2, "easy",     "イージー走",      8,  "低強度ジョグ（5:20〜5:40/km）— 会話できるペース"),
    (3, "tempo",    "ペース変化走",   10,  "前半 6km（5:20/km）→ ラスト 4km（4:35〜4:40/km）に上げる"),
    (4, "rest",     "休養",           0,  "完全休養 or 軽ジョグ（5:40/km 以上・5km 以内）"),
    (5, "interval", "インターバル",    8,  "W-up 2km ＋ 1000m×5本（3:48〜3:54/km）休息2分 ＋ C-down 2km"),
    (6, "long",     "ロング走",       15,  "低強度ジョグ 15〜20km（5:00〜5:30/km）— 脚を使い切らない"),
]
_WEEKLY_PLAN_RANGE = "45〜50"  # 表示用レンジ（実計は sum of target_km）

def _detect_plan_type(day_runs):
    """複数ランの中で最長をもとに練習種別を推定"""
    if not day_runs: return None
    main = max(day_runs, key=lambda r: float(r.get("distance_km") or 0))
    label = training_type(main)[0]
    return {"ロング走": "long", "インターバル": "interval",
            "テンポ走": "tempo", "イージー走": "easy"}.get(label, "easy")

def build_weekly_menu(runs):
    """今週（月曜始まり）の固定テンプレートに対する実績評価HTML"""
    today    = date.today()
    mon      = today - timedelta(days=today.weekday())   # 今週の月曜（weekday 0=Mon）
    week_end = mon + timedelta(days=6)
    day_labels = ["月", "火", "水", "木", "金", "土", "日"]

    ai_week = resolve_ai_weekly_plan(TARGET_YEAR, TARGET_MONTH, today)
    if ai_week:
        weekly_plan = ai_week["plan"]
        plan_by_date = ai_week.get("plan_by_date") or {}
        weekly_plan_range = ai_week["range_km"]
        plan_subtitle = ai_week["subtitle"]
        plan_title_extra = ai_week.get("week_title", "")
    else:
        weekly_plan = _WEEKLY_PLAN
        plan_by_date = {}
        weekly_plan_range = _WEEKLY_PLAN_RANGE
        plan_subtitle = "固定プランに対する実績を自動評価。月曜始まり・VDOT 51（現走力）基準。"
        plan_title_extra = ""

    # ── 月またぎ対応：前後月のCSVも読み込んで今週データを完成させる ──────
    # 月またぎ週は両月のレポートに同じ内容を表示する（リダイレクト廃止）
    all_runs = list(runs)

    # 週が前月にはみ出す場合（例: 月曜が先月）→ 前月CSV読み込み
    if mon.month != TARGET_MONTH or mon.year != TARGET_YEAR:
        pm_year  = TARGET_YEAR if TARGET_MONTH > 1 else TARGET_YEAR - 1
        pm_month = TARGET_MONTH - 1 if TARGET_MONTH > 1 else 12
        prev_csv = f"runs_{pm_year}{pm_month:02d}.csv"
        if os.path.exists(prev_csv):
            extra = [r for r in load_csv(prev_csv)
                     if float(r.get("distance_km") or 0) >= 0.5]
            all_runs.extend(extra)

    # 週が翌月にはみ出す場合（例: 日曜が来月）→ 翌月CSV読み込み
    if week_end.month != TARGET_MONTH or week_end.year != TARGET_YEAR:
        nm_year  = TARGET_YEAR + (1 if TARGET_MONTH == 12 else 0)
        nm_month = 1 if TARGET_MONTH == 12 else TARGET_MONTH + 1
        next_csv = f"runs_{nm_year}{nm_month:02d}.csv"
        if os.path.exists(next_csv):
            extra = [r for r in load_csv(next_csv)
                     if float(r.get("distance_km") or 0) >= 0.5]
            all_runs.extend(extra)

    week_dates = [mon + timedelta(days=i) for i in range(7)]

    # 今週の実績をdateごとに集める（複数走行対応）
    runs_by_date = defaultdict(list)
    for r in all_runs:
        try:
            d = date.fromisoformat(r["date"])
            if mon <= d <= week_end:
                runs_by_date[d].append(r)
        except: pass

    rows           = ""
    week_actual_km = 0.0
    week_target_km = 0.0
    _type_names    = {"easy": "イージー", "interval": "インターバル",
                      "tempo": "テンポ走",  "long": "ロング走", "rest": "休養"}

    for i, d in enumerate(week_dates):
        if plan_by_date:
            _, plan_type, plan_label, plan_dist, plan_desc = plan_by_date.get(
                d, (i, "rest", "—", 0.0, "AI提案の対象週外")
            )
            if d in plan_by_date:
                week_target_km += plan_dist
        else:
            _, plan_type, plan_label, plan_dist, plan_desc = weekly_plan[i]
            week_target_km += plan_dist
        day_runs  = runs_by_date.get(d, [])
        actual_km = sum(float(r.get("distance_km") or 0) for r in day_runs)
        week_actual_km += actual_km
        is_today  = (d == today)
        is_future = (d > today)

        # ── 評価ロジック ──────────────────────────────────────────────────
        if plan_type == "rest":
            if actual_km > 3:
                icon, icolor = "⚠️", "#f59e0b"
                eval_txt = f"予定休養日 → {actual_km:.1f}km 走行。疲労蓄積に注意。"
            elif actual_km > 0:
                icon, icolor = "✅", "#22c55e"
                eval_txt = f"軽めジョグ {actual_km:.1f}km。回復促進に有効。"
            elif is_future or is_today:
                icon, icolor = "⬜", "#a0aec0"
                eval_txt = plan_desc
            else:
                icon, icolor = "✅", "#22c55e"
                eval_txt = "休養完了"
        elif is_future:
            icon, icolor = "⬜", "#a0aec0"
            eval_txt = plan_desc
        elif actual_km == 0:
            icon, icolor = "❌", "#ef4444"
            eval_txt = f"未実施（予定: {plan_label} {plan_dist}km）"
        else:
            actual_type = _detect_plan_type(day_runs)
            main_run    = max(day_runs, key=lambda r: float(r.get("distance_km") or 0))
            pace_str    = main_run.get("pace_per_km", "-")
            plan_name   = _type_names.get(plan_type, plan_label)
            actual_name = _type_names.get(actual_type, "")

            if actual_km >= plan_dist * 0.7 and actual_type == plan_type:
                icon, icolor = "✅", "#22c55e"
                eval_txt = f"◎ {actual_km:.1f}km / {pace_str}/km — {plan_name}として良好"
            elif actual_km >= plan_dist * 0.7 and actual_type != plan_type:
                icon, icolor = "🔄", "#3b82f6"
                if plan_type == "easy" and actual_type in ("interval", "tempo"):
                    eval_txt = f"⚡ {plan_name}予定 → {actual_name}（強度高め / {actual_km:.1f}km）。翌日休養推奨。"
                elif plan_type in ("interval", "tempo") and actual_type == "easy":
                    eval_txt = f"🔄 {plan_name}予定 → {actual_name}（回復優先 / {actual_km:.1f}km）。翌週補填を検討。"
                elif plan_type == "long" and actual_type != "long":
                    eval_txt = f"📏 ロング走予定 → {actual_name} {actual_km:.1f}km / {pace_str}/km。ロングは来週補填を。"
                else:
                    eval_txt = f"🔄 {plan_name}予定 → {actual_name} {actual_km:.1f}km / {pace_str}/km"
            else:
                icon, icolor = "⚠️", "#f59e0b"
                eval_txt = f"{plan_dist}km 予定 → {actual_km:.1f}km（短縮） / {pace_str}/km"

        # 複数ラン詳細タグ
        run_tags = ""
        if day_runs:
            parts = [f"{float(r.get('distance_km') or 0):.1f}km/{r.get('pace_per_km','-')}/km"
                     for r in day_runs]
            run_tags = f'<span style="font-size:11px;color:#718096;margin-left:6px">({" + ".join(parts)})</span>'

        today_bg  = "background:#fffbeb;" if is_today else ""
        dist_str  = f"{plan_dist}km" if plan_dist > 0 else "—"
        day_color = "#fc4c02" if is_today else "#4a5568"
        day_fw    = "700" if is_today else "400"

        rows += f"""
        <tr style="{today_bg}">
          <td style="width:28px;text-align:center;font-size:15px;padding:8px 4px">{icon}</td>
          <td style="width:28px;font-weight:{day_fw};color:{day_color};font-size:13px;padding:8px 4px">{day_labels[i]}</td>
          <td style="font-size:13px;font-weight:600;color:#2d3748;padding:8px 8px;white-space:nowrap">
            {plan_label} <span style="font-size:11px;font-weight:400;color:#a0aec0">{dist_str}</span>
          </td>
          <td style="font-size:12px;color:{icolor};padding:8px 8px;line-height:1.6">
            {eval_txt}{run_tags}
          </td>
        </tr>"""

    week_start_str = mon.strftime("%-m/%-d")
    week_end_str   = (mon + timedelta(days=6)).strftime("%-m/%-d")
    remaining_km   = max(0, week_target_km - week_actual_km)
    prog_pct       = min(100, round(week_actual_km / week_target_km * 100)) if week_target_km else 0
    prog_color     = "#22c55e" if week_actual_km >= week_target_km else "#3b82f6"
    status_txt     = "✅ 週目標達成！" if week_actual_km >= week_target_km else f"残り {remaining_km:.1f}km"

    title_suffix = f" / {html_module.escape(plan_title_extra)}" if plan_title_extra else ""
    return f"""
    <div class="plan-box" style="margin-bottom:24px">
      <div class="plan-label">📅 今週の練習メニュー（{week_start_str}〜{week_end_str} / 週{weekly_plan_range}km目標）{title_suffix}</div>
      <div style="font-size:11px;color:#a0aec0;margin-bottom:10px">
        {html_module.escape(plan_subtitle)}
      </div>
      <table style="width:100%;border-collapse:collapse;margin-bottom:12px">
        <tbody>{rows}</tbody>
      </table>
      <div style="height:8px;background:#e2e8f0;border-radius:4px;margin-bottom:8px;overflow:hidden">
        <div style="height:8px;background:{prog_color};border-radius:4px;width:{prog_pct}%"></div>
      </div>
      <div style="display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:#718096">
        <span>週実績: <strong style="color:#2d3748">{week_actual_km:.1f}km</strong></span>
        <span>週目標: <strong>{week_target_km}km</strong></span>
        <span style="color:{prog_color};font-weight:700">{status_txt}</span>
      </div>
    </div>"""


# ── 10点満点スコアリング ──────────────────────────────────────────────────
def score_run(run, laps, run_type):
    """
    4軸 × 2.5点 = 10点満点
    ペース適切度 / 心拍ゾーン / ラップ一貫性 / 強度・量バランス
    """
    pace  = pace_to_sec(run.get("pace_per_km"))
    hr    = float(run.get("avg_heartrate") or 0)
    dist  = float(run.get("distance_km") or 0)
    sec   = parse_time_sec(run.get("moving_time"))

    def clamp(v, lo, hi): return max(lo, min(hi, v))

    # ── ペース適切度 (0-2.5) ── VO2Max 58 / VDOT 58 基準 ─────────────
    if run_type == "race":
        pace_score = 2.5  # レースは実力を出し切ったとみなす
    elif run_type == "long":
        # E〜M ペース (4:26-5:15) が理想。速すぎると減点
        if not pace: pace_score = 1.0
        elif 266 <= pace <= 315: pace_score = 2.5   # 4:26-5:15 ◎ M〜E zone
        elif 255 <= pace < 266:  pace_score = 1.5   # 4:15-4:26 やや速い
        elif pace > 315:         pace_score = 2.0   # 5:15+ OK だが遅め
        else:                    pace_score = 0.5   # 4:15 より速い NG
    elif run_type == "interval":
        # インターバル本番ラップの平均ペースで評価（W-up/C-down を除外）
        efforts    = extract_interval_efforts(laps)
        eff_paces  = [pace_to_sec(e.get("pace_per_km")) for e in efforts
                      if pace_to_sec(e.get("pace_per_km"))]
        eval_pace  = int(sum(eff_paces) / len(eff_paces)) if eff_paces else pace

        if not eval_pace:            pace_score = 1.0
        elif eval_pace <= _TRAIN_I_LO:          pace_score = 2.5  # 〜3:48 ◎ 設定より速め
        elif eval_pace <= _TRAIN_I_HI:          pace_score = 2.5  # 〜3:54 ◎ 設定通り
        elif eval_pace <= _TRAIN_I_HI + 8:      pace_score = 2.0  # 〜4:02 わずかに遅め
        elif eval_pace <= _TRAIN_I_HI + 20:     pace_score = 1.5  # 〜4:14 遅め
        else:                                    pace_score = 0.5
    elif run_type == "tempo":
        # T ペース 4:07-4:14/km (247-254秒) が理想
        if not pace: pace_score = 1.0
        elif 247 <= pace <= 254: pace_score = 2.5   # 4:07-4:14 ◎ T zone
        elif 240 <= pace < 247:  pace_score = 2.0   # やや速い
        elif 254 <= pace <= 270: pace_score = 2.0   # やや遅い M-T 境界
        elif 235 <= pace < 240:  pace_score = 1.0
        else:                    pace_score = 0.5
    else:  # easy
        # E ペース 4:53-5:15/km (293-315秒) が理想
        if not pace: pace_score = 1.0
        elif 293 <= pace <= 315: pace_score = 2.5   # 4:53-5:15 ◎ E zone
        elif 270 <= pace < 293:  pace_score = 1.5   # 4:30-4:53 やや速い
        elif pace > 315:         pace_score = 2.0   # 5:15+ 十分遅い
        else:                    pace_score = 0.5

    # ── 心拍ゾーン (0-2.5) ──────────────────────────────
    if not hr:
        hr_score = 1.5
    elif run_type == "race":
        hr_score = 2.5 if hr >= 158 else 1.5
    elif run_type == "long":
        if hr < 152:    hr_score = 2.5
        elif hr < 160:  hr_score = 2.0
        elif hr < 166:  hr_score = 1.0
        else:           hr_score = 0.5
    elif run_type == "interval":
        if hr >= 175:   hr_score = 2.5
        elif hr >= 168: hr_score = 2.0
        elif hr >= 160: hr_score = 1.0
        else:           hr_score = 0.5
    elif run_type == "tempo":
        if 155 <= hr <= 167:  hr_score = 2.5
        elif 150 <= hr < 155: hr_score = 1.5
        elif hr > 167:        hr_score = 1.5
        else:                 hr_score = 0.5
    else:  # easy
        if hr < 148:    hr_score = 2.5
        elif hr < 155:  hr_score = 2.0
        elif hr < 162:  hr_score = 1.0
        else:           hr_score = 0.5

    # ── ラップ一貫性 (0-2.5) ─────────────────────────────
    lp_avg, lp_std = lap_pace_stats(laps)
    if lp_std is None or run_type == "interval":
        # インターバルはオン/オフ交互なので一貫性スコアは固定
        cons_score = 2.0 if run_type == "interval" else 1.5
    else:
        cv = lp_std / lp_avg * 100 if lp_avg else 99
        if cv < 4:      cons_score = 2.5
        elif cv < 8:    cons_score = 2.0
        elif cv < 14:   cons_score = 1.0
        else:           cons_score = 0.5

    # ── 強度・量バランス (0-2.5) ─────────────────────────
    if run_type == "race":
        bal_score = 2.5
    elif run_type == "long":
        if 20 <= dist <= 35:  bal_score = 2.5
        elif 18 <= dist < 20: bal_score = 2.0
        else:                  bal_score = 1.0
    elif run_type == "interval":
        if 4 <= dist <= 12:  bal_score = 2.5
        elif dist < 4:       bal_score = 1.0
        else:                bal_score = 2.0
    elif run_type == "tempo":
        mins = sec / 60
        if 20 <= mins <= 45: bal_score = 2.5
        elif 15 <= mins < 20: bal_score = 2.0
        else:                 bal_score = 1.5
    else:  # easy
        if 6 <= dist <= 15:  bal_score = 2.5
        elif dist >= 5:      bal_score = 2.0
        else:                bal_score = 1.5

    total = pace_score + hr_score + cons_score + bal_score
    return round(clamp(total, 1.0, 10.0), 1), {
        "pace": pace_score, "hr": hr_score,
        "cons": cons_score, "bal": bal_score,
    }

def score_stars(score):
    """スコアを星＋数値で表示するHTML"""
    if score >= 8.5:   color, label = "#22c55e", "優秀"
    elif score >= 7.0: color, label = "#84cc16", "良好"
    elif score >= 5.5: color, label = "#f59e0b", "普通"
    elif score >= 4.0: color, label = "#f97316", "要改善"
    else:              color, label = "#ef4444", "課題あり"
    bar_w = int(score / 10 * 100)
    return f"""<div class="score-wrap">
      <span class="score-num" style="color:{color}">{score}</span>
      <span class="score-unit">/10</span>
      <span class="score-label" style="background:{color}">{label}</span>
      <div class="score-bar-bg"><div class="score-bar-fg" style="width:{bar_w}%;background:{color}"></div></div>
    </div>"""

# ── コーチングコメント（ルールベース・複数メソッド対応） ──────────────────
# VO2Max 58 / VDOT 58 ベース（Garmin 計測）
# E pace: 4:53-5:15  M pace: 4:26  T pace: 4:07-4:14
# I pace: 3:43       R pace: 3:29
# 参照メソッド: ダニエルズ / Pfitzinger / Hansons / 80:20 / McMillan

def lap_pace_stats(lp_list):
    """ラップのペース標準偏差を返す（秒/km）"""
    secs = [pace_to_sec(l.get("pace_per_km")) for l in lp_list]
    secs = [s for s in secs if s and s < 600]  # 10分/km 超は除外（停止等）
    if len(secs) < 2: return None, None
    avg = sum(secs) / len(secs)
    var = sum((s - avg)**2 for s in secs) / len(secs)
    return avg, var**0.5

def extract_interval_efforts(laps):
    """
    ラップデータからインターバル本番ラップを抽出。
    条件: 距離 0.8〜1.3km かつ ペース 4:10/km（250秒）以内
    W-up / C-down / リカバリージョグ は除外される。
    """
    efforts = []
    for lap in laps:
        dist = float(lap.get("distance_km") or 0)
        pace = pace_to_sec(lap.get("pace_per_km"))
        if not pace or pace <= 0: continue
        if 0.8 <= dist <= 1.3 and pace <= 250:
            efforts.append(lap)
    return efforts

def coaching_comment(run, laps):
    dist  = float(run.get("distance_km") or 0)
    pace  = pace_to_sec(run.get("pace_per_km"))
    hr    = float(run.get("avg_heartrate") or 0)
    maxhr = float(run.get("max_heartrate") or 0)
    elev  = float(run.get("elevation_gain_m") or 0)
    name  = run.get("name", "")
    dt    = run.get("date", "")

    title, color, purpose, assessment, tips = "", "#22c55e", "", "", []

    # ── 種別判定 ──────────────────────────────────────────────────
    is_race    = dist >= 40
    is_long    = dist >= 18 and not is_race
    is_interval= (hr >= 170 or (pace and pace <= 255)) and dist < 10  # 4:15/km 以下
    is_tempo   = (hr >= 155 or (pace and pace <= 280)) and dist < 18 and not is_interval
    is_easy    = not any([is_race, is_long, is_interval, is_tempo])

    if is_race:
        title, color = "🏆 レース", "#dc2626"
        purpose = "最高のパフォーマンスを引き出す本番レース。"
        if pace:
            finish_sec = pace * 42.195
            h2, r2 = divmod(int(finish_sec), 3600); m2 = r2 // 60
            assessment = (
                f"42.7km を {run['pace_per_km']}/km ペースで完走。"
                f"42.195km 換算タイム <strong>約 {h2}:{m2:02d}</strong>。"
            )
            if pace <= 280:
                assessment += " サブ3:20 ペースの高水準なレースでした。"
            if hr >= 160:
                assessment += f" 平均心拍 {hr:.0f}bpm はレースとして適切な高強度域。"
        tips = [
            "レース後は 1km あたり 1日 の回復期間が目安（42km → 約6週間の完全回復）",
            "最初の1〜2週間はジョグのみ。スピード練習は再開しない",
            "筋肉痛が消えても心肺・ホルモン系の疲労は数週残る",
        ]

    elif is_long:
        title, color = "🏃 ロング走", "#6366f1"
        purpose = (
            "有酸素基礎能力の構築・脂質代謝の向上・精神的耐久力の養成が目的。"
            "ダニエルズ理論では週間距離の 25〜30%・最大 2.5〜3時間 が推奨。"
        )
        if pace:
            if pace <= 270:  # 4:30/km 以下
                assessment = (
                    f"{dist:.1f}km を {run['pace_per_km']}/km で走行。"
                    f" ペースが速め（推奨 E ペース 5:10〜5:30）。"
                    f" HR {hr:.0f}bpm は{'高め（閾値近く）' if hr >= 160 else '許容範囲'}。"
                    " ロング走は「会話できるペース」が原則。速すぎると疲労が蓄積します。"
                )
            else:
                assessment = (
                    f"{dist:.1f}km を {run['pace_per_km']}/km で走行。"
                    f" E ペース域での適切なロング走。HR {hr:.0f}bpm は{'やや高め' if hr >= 158 else '良好'}。"
                )
        if elev > 100:
            tips.append(f"獲得標高 {elev:.0f}m あり。平地換算では +{elev/10:.0f}秒/km 程度のロード")
        tips += [
            "走行後は30分以内に糖質＋タンパク質を補給（回復促進）",
            "翌日は完全休養かリカバリージョグ（5:30/km 以上）を推奨",
        ]

    elif is_interval:
        title, color = "⚡ インターバル / I-pace", "#ef4444"
        purpose = (
            "VO₂max向上・スピード持久力養成が主目的。"
            "現在のプラン（VDOT 51 / 5km PB 19:37 基準）："
            "<strong>1000m×5本（3:48〜3:54/km）・休息2分</strong>。"
            " <strong>Pfitzinger</strong> では VO₂max インターバルを週1回のみ実施を推奨。"
            " Sub-3:00 へのスピード基盤を作る最重要セッション。"
        )

        # ラップデータからインターバル本番ラップを抽出
        efforts = extract_interval_efforts(laps)
        n_eff   = len(efforts)

        if efforts:
            eff_paces = [pace_to_sec(e.get("pace_per_km")) for e in efforts
                         if pace_to_sec(e.get("pace_per_km"))]
            eff_avg = int(sum(eff_paces) / len(eff_paces)) if eff_paces else None
            eff_min = min(eff_paces) if eff_paces else None
            eff_max = max(eff_paces) if eff_paces else None

            if eff_avg:
                # 目標 3:48〜3:54/km（228〜234秒）との比較
                if eff_avg <= _TRAIN_I_LO:
                    p_eval, p_col = "◎ 設定より速め", "#22c55e"
                elif eff_avg <= _TRAIN_I_HI:
                    p_eval, p_col = "◎ 設定ペース通り", "#22c55e"
                elif eff_avg <= _TRAIN_I_HI + 8:
                    p_eval, p_col = f"△ 設定 +{eff_avg - _TRAIN_I_HI}秒 — わずかに遅め", "#f59e0b"
                else:
                    p_eval, p_col = f"▽ 設定 +{eff_avg - _TRAIN_I_HI}秒/km — 遅め", "#ef4444"

                assessment = (
                    f"<strong>{n_eff}本 検出</strong>（目標 5本）。"
                    f"努力ペース平均: <strong style='color:{p_col}'>{fmt_time(eff_avg)}/km — {p_eval}</strong>。"
                    f"最速 {fmt_time(eff_min)}〜最遅 {fmt_time(eff_max)}/km。HR {hr:.0f}/{maxhr:.0f}bpm。"
                )

                # ペースのばらつき評価
                if len(eff_paces) >= 2:
                    std = (sum((p - eff_avg)**2 for p in eff_paces) / len(eff_paces)) ** 0.5
                    if std < 5:
                        assessment += " <strong>全本のペースが揃っており理想的なイーブンラン ✅</strong>。"
                    elif std < 12:
                        assessment += f" ペースばらつき ±{std:.0f}秒 — 許容範囲内。"
                    else:
                        assessment += f" ペースばらつき ±{std:.0f}秒 — 1本目を少し抑えてイーブンを意識。"

            # 本数の評価
            if n_eff < 5:
                assessment += f" <span style='color:#f59e0b'>⚠ {n_eff}本完了（目標5本）。</span>"
            elif n_eff == 5:
                assessment += " <strong>5本完遂 ✅</strong>"
            else:
                assessment += f" {n_eff}本実施（目標超え）。"
        else:
            # ラップ未取得 or 構造化インターバル未検出 → 全体ペースで代替評価
            if pace:
                assessment = (
                    f"{dist:.1f}km、全体平均 {run['pace_per_km']}/km、HR {hr:.0f}/{maxhr:.0f}bpm。"
                    " （Garmin のラップデータが未取得のため、個別本数の評価はできません）"
                )
            else:
                assessment = f"{dist:.1f}km、HR {hr:.0f}bpm。ラップデータ未取得。"

        if hr >= 175:
            assessment += " 心拍が VO₂max 域に達しており追い込めています。"
        elif hr >= 168:
            assessment += " 心拍はVO₂max 域に近い。最後のレップで 175+ を狙えると理想的。"

        tips = [
            "【設定】1000m×5本（3:48〜3:54/km）・休息2分（200mジョグ or 静止）",
            "ステップアップ順：① 3:54で5本揃える → ② 休息を1:30→1:00に短縮 → ③ 3:48に上げる",
            "W-up 2km（5:20/km 以上）+ C-down 2km 必須。1本目は必ず抑えて入ること",
            "週1回厳守 — 回復不足は全セッションの質を下げる",
        ]

    elif is_tempo:
        title, color = "🔥 テンポ走 / T-pace", "#f59e0b"
        purpose = (
            "乳酸閾値（LT）の向上が目的。"
            "<strong>ダニエルズ理論</strong>の T ペース（<strong>4:07〜4:14/km — VO₂Max 58</strong>）で"
            " 20〜40分間維持する。"
            " <strong>Hansons</strong> 式では「テンポ」をレースペースより 15秒/km 遅いペースで"
            " 10〜16km 行うことで累積疲労への耐性も養う。"
        )
        lp_avg, lp_std = lap_pace_stats(laps)
        if pace:
            diff = pace - ((_T_LO + _T_HI) // 2)
            if pace <= _T_HI + 10:
                assessment = f"ペース {run['pace_per_km']}/km は T ペース域（目標 4:07-4:14）。HR {hr:.0f}bpm。"
            elif pace <= _M_PACE + 10:
                assessment = f"ペース {run['pace_per_km']}/km は M〜T ペース域。HR {hr:.0f}bpm。マラソンペース走として有効なセッション。"
            else:
                assessment = f"ペース {run['pace_per_km']}/km、HR {hr:.0f}bpm。E 走に近い強度ですが積み上げとして有効。"
        if lp_std is not None and lp_avg:
            cv = lp_std / lp_avg * 100
            if cv < 6:
                assessment += " <strong>ラップが安定しており、テンポ走として質の高いセッション</strong>。"
            elif cv < 12:
                assessment += " ラップのばらつきは許容範囲内。"
            else:
                assessment += " ラップペースの波が大きめ。一定強度を維持する意識を。"
        tips = [
            "【ダニエルズ】T ペース（4:07〜4:14/km）を 20〜30分連続 or 1km × 5〜6本",
            "【Hansons】マラソンペース+15秒（4:41/km）で 10〜16km のテンポも有効",
            "「会話はできないが一定強度を維持できる」感覚（10段階の7〜8）",
            "テンポ走はインターバルと同週の場合は各1回まで",
        ]

    else:  # Easy
        title, color = "🟢 イージー走 / E-pace", "#22c55e"
        purpose = (
            "有酸素基礎・毛細血管の発達・筋腱の強化・回復促進が目的。"
            "<strong>ダニエルズ理論</strong>では週間走行量の 70〜80% をこの強度で行うことを推奨。"
            " <strong>80/20 ルール（Matt Fitzgerald）</strong> では全練習の 80% を閾値以下で走ることで"
            " 長期的なパフォーマンス向上が実証されている。"
            f" E ペース目安：<strong>4:53〜5:15/km</strong>（VO₂Max 58 基準）。"
        )
        if pace:
            if _E_LO <= pace <= _E_HI:
                assessment = (
                    f"{dist:.1f}km を {run['pace_per_km']}/km、HR {hr:.0f}bpm。"
                    " <strong>◎ 理想的な E ペース域</strong>。有酸素適応と回復を両立する良質なセッション。"
                )
            elif pace < _E_LO:
                diff_sec = _E_LO - pace
                assessment = (
                    f"{dist:.1f}km を {run['pace_per_km']}/km、HR {hr:.0f}bpm。"
                    f" E ペース（4:53/km）より {diff_sec}秒/km 速め。"
                    " 80/20 の観点では E 走は「会話できるペース」が鉄則。もう少しゆっくりでも効果は同じです。"
                )
            else:
                assessment = (
                    f"{dist:.1f}km を {run['pace_per_km']}/km、HR {hr:.0f}bpm。"
                    " ゆったりとした回復走。筋肉・腱の修復を促す貴重なセッション。"
                )
        if hr < 145:
            tips.append("心拍が低く、回復走として完璧に機能しています（Maffetone MAF ゾーン）")
        elif hr > 158:
            tips.append(f"HR {hr:.0f}bpm はやや高め。次回は 5:10/km 以上にするとE走効果が高まります")
        tips += [
            "「鼻呼吸で走れるペース」「隣の人と会話できるペース」が目安",
            "【80/20 ルール】週の練習の 80% はこの強度に。土台なくして記録更新なし",
        ]

    # ラップ詳細補足（共通）
    if laps and len(laps) > 3 and not is_race:
        lp_avg, lp_std = lap_pace_stats(laps)
        fastest_lap = min((l for l in laps if pace_to_sec(l.get("pace_per_km"))),
                          key=lambda l: pace_to_sec(l.get("pace_per_km")), default=None)
        slowest_lap = max((l for l in laps if pace_to_sec(l.get("pace_per_km")) and
                           pace_to_sec(l.get("pace_per_km")) < 600),
                          key=lambda l: pace_to_sec(l.get("pace_per_km")), default=None)
        if fastest_lap and slowest_lap:
            fp = fastest_lap.get("pace_per_km", "-")
            sp = slowest_lap.get("pace_per_km", "-")
            if fp != sp:
                tips.append(f"最速ラップ {fp}/km、最遅ラップ {sp}/km")

    run_type_key = ("race" if is_race else "long" if is_long else
                    "interval" if is_interval else "tempo" if is_tempo else "easy")
    return {
        "title": title, "color": color, "run_type": run_type_key,
        "purpose": purpose, "assessment": assessment, "tips": tips,
    }

def coaching_sections(gps_map):
    sections = []
    target = sorted([r for r in runs if float(r.get("distance_km") or 0) >= 1.5],
                    key=lambda r: r["date"], reverse=True)
    for r in target:
        aid  = str(r["activity_id"])
        lp   = laps_by_id.get(aid, [])
        c    = coaching_comment(r, lp)
        sc, breakdown = score_run(r, lp, c["run_type"])
        tips_html = "".join(f"<li>{t}</li>" for t in c["tips"]) if c["tips"] else ""

        # マップ
        coords = gps_map.get(aid, [])
        if coords:
            map_id  = f"map_{aid}"
            coords_js = json.dumps(coords)
            center = coords[len(coords)//2]
            map_html = f"""
            <div id="{map_id}" class="run-map"></div>
            <script>
            (function(){{
              var m = L.map('{map_id}', {{zoomControl:true, attributionControl:false}});
              L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png').addTo(m);
              var coords = {coords_js};
              var poly = L.polyline(coords, {{color:'{c["color"]}',weight:3,opacity:0.85}}).addTo(m);
              m.fitBounds(poly.getBounds(), {{padding:[12,12]}});
              L.circleMarker(coords[0], {{radius:6,color:'#fff',fillColor:'#22c55e',fillOpacity:1,weight:2}}).addTo(m);
              L.circleMarker(coords[coords.length-1], {{radius:6,color:'#fff',fillColor:'#ef4444',fillOpacity:1,weight:2}}).addTo(m);
            }})();
            </script>"""
        else:
            map_html = '<div class="run-map no-map">GPS データなし</div>'

        # スコア内訳
        bd = breakdown
        breakdown_html = f"""
        <div class="score-breakdown">
          <span title="ペース適切度">ペース {bd['pace']:.1f}</span>
          <span title="心拍ゾーン">心拍 {bd['hr']:.1f}</span>
          <span title="ラップ一貫性">一貫性 {bd['cons']:.1f}</span>
          <span title="強度・量バランス">バランス {bd['bal']:.1f}</span>
          <small style="color:#999">（各2.5点満点）</small>
        </div>"""

        sections.append(f"""
        <div class="coach-card">
          <div class="coach-header" style="border-left:4px solid {c['color']}">
            <span class="coach-badge" style="background:{c['color']}">{c['title']}</span>
            <span class="coach-date">{r['date']} ({r.get('weekday','')}) — {r['name']}
              <small>{float(r['distance_km']):.1f}km / {r.get('pace_per_km','-')}/km / HR {r.get('avg_heartrate','-')} / ⏱ {r.get('moving_time','-')}</small>
            </span>
            <div style="margin-left:auto">{score_stars(sc)}</div>
          </div>
          <div class="coach-layout">
            <div class="coach-body">
              {breakdown_html}
              <div class="coach-purpose"><strong>🎯 練習の目的：</strong>{c['purpose']}</div>
              <div class="coach-assessment"><strong>📊 評価：</strong>{c['assessment']}</div>
              {"<ul class='coach-tips'>" + tips_html + "</ul>" if tips_html else ""}
            </div>
            {map_html}
          </div>
        </div>""")
    return "\n".join(sections)

# ── データ集計 ─────────────────────────────────────────────────────────────
runs = load_csv(RUNS_CSV)
laps = load_csv(LAPS_CSV)
gps_map = load_gps()

# フィルタ: 距離 0.5km 未満は除外
runs = [r for r in runs if float(r.get("distance_km") or 0) >= 0.5]

total_dist = sum(float(r["distance_km"] or 0) for r in runs)
total_sec  = sum(parse_time_sec(r["moving_time"]) for r in runs)
total_elev = sum(float(r["elevation_gain_m"] or 0) for r in runs)
hr_vals    = [float(r["avg_heartrate"]) for r in runs if r.get("avg_heartrate")]
avg_hr     = sum(hr_vals)/len(hr_vals) if hr_vals else 0

# 週別
by_week = defaultdict(lambda: {"dist": 0.0, "runs": 0, "sec": 0})
for r in runs:
    try:
        d = date.fromisoformat(r["date"])
        w = (d - MONTH_START).days // 7 + 1
        by_week[w]["dist"] += float(r["distance_km"] or 0)
        by_week[w]["runs"] += 1
        by_week[w]["sec"]  += parse_time_sec(r["moving_time"])
    except: pass

# ラップ辞書
laps_by_id = defaultdict(list)
for lap in laps: laps_by_id[lap["activity_id"]].append(lap)

# ── グラフ用データ ─────────────────────────────────────────────────────────
# 1. 週別距離
week_labels = [week_label(r["date"]) for r in runs]
week_dists  = [round(by_week[w]["dist"], 1) for w in sorted(by_week)]
week_keys   = [f"第{w}週" for w in sorted(by_week)]

# 2. ランごとのペース推移（日付順）
sorted_runs = sorted(runs, key=lambda r: r["date"])
pace_labels = [r["date"][5:] + " " + r["name"][:6] for r in sorted_runs]
pace_data   = [round(pace_to_sec(r.get("pace_per_km")) / 60, 2)
               if pace_to_sec(r.get("pace_per_km")) else None for r in sorted_runs]
dist_data   = [float(r["distance_km"] or 0) for r in sorted_runs]
hr_data     = [float(r["avg_heartrate"] or 0) or None for r in sorted_runs]

# 3. 種別内訳 (ドーナツ)
type_count = defaultdict(int)
for r in runs: type_count[training_type(r)[0]] += 1
type_labels = list(type_count.keys())
type_values = [type_count[k] for k in type_labels]
type_colors = [training_type({"avg_heartrate": 0, "distance_km": 0, "pace_per_km": None,
                               **{r["name"]: r for r in runs if training_type(r)[0]==k}.get(k, {})})[1]
               if False else "#6366f1" for k in type_labels]
type_colors = []
for k in type_labels:
    for r in runs:
        if training_type(r)[0] == k:
            type_colors.append(training_type(r)[1]); break

# ── アクティビティ行 HTML ─────────────────────────────────────────────────
def activity_rows():
    rows = []
    for r in sorted(runs, key=lambda x: x["date"], reverse=True):
        typ, col = training_type(r)
        dist = float(r["distance_km"] or 0)
        hr   = r.get("avg_heartrate") or "-"
        pace = r.get("pace_per_km") or "-"
        elev = f"{float(r.get('elevation_gain_m') or 0):.0f}m"
        badge = f'<span style="background:{col};color:#fff;padding:2px 8px;border-radius:12px;font-size:11px">{typ}</span>'
        rows.append(f"""
        <tr>
          <td>{r['date']} <small style="color:#888">({r.get('weekday','')})</small></td>
          <td>{r['name']}</td>
          <td style="text-align:right;font-weight:bold">{dist:.1f} km</td>
          <td style="text-align:right">{r.get('moving_time','-')}</td>
          <td style="text-align:right">{pace} /km</td>
          <td style="text-align:right">{hr}</td>
          <td class="act-col-elev" style="text-align:right">{elev}</td>
          <td>{badge}</td>
        </tr>""")
    return "\n".join(rows)

def lap_sections():
    sections = []
    for r in sorted(runs, key=lambda x: x["date"], reverse=True):
        aid = str(r["activity_id"])
        lp  = laps_by_id.get(aid, [])
        if len(lp) <= 1: continue
        rows = ""
        for lap in lp:
            hr = lap.get("avg_heartrate") or "-"
            rows += f"""<tr>
              <td style="text-align:center">{lap['lap_index']}</td>
              <td style="text-align:right">{float(lap['distance_km'] or 0):.2f} km</td>
              <td style="text-align:right">{lap.get('moving_time','-')}</td>
              <td style="text-align:right">{lap.get('pace_per_km','-')}</td>
              <td style="text-align:right">{hr}</td>
            </tr>"""
        sections.append(f"""
        <div class="lap-card">
          <div class="lap-title">{r['date']} — {r['name']}
            <small style="color:#888;font-weight:normal">（{float(r['distance_km']):.1f}km）</small>
          </div>
          <table class="lap-table">
            <thead><tr><th>Lap</th><th>距離</th><th>タイム</th><th>ペース</th><th>HR</th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </div>""")
    return "\n".join(sections)

# ── AI 月次コーチング（coach_claude.py 等の出力） ───────────────────────────
def _inline_md(text: str) -> str:
    escaped = html_module.escape(text)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)


def md_to_html(md: str) -> str:
    lines = md.splitlines()
    out: list[str] = []
    in_ul = False
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()
        s = line.strip()
        if not s:
            if in_ul:
                out.append("</ul>")
                in_ul = False
            i += 1
            continue
        if s.startswith("|") and "|" in s[1:]:
            if in_ul:
                out.append("</ul>")
                in_ul = False
            table_rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if row and not all(re.match(r"^[-:\s]+$", c) for c in row):
                    table_rows.append(row)
                i += 1
            if table_rows:
                out.append('<table class="md-table"><tbody>')
                for ri, row in enumerate(table_rows):
                    tag = "th" if ri == 0 else "td"
                    out.append(
                        "<tr>"
                        + "".join(f"<{tag}>{_inline_md(c)}</{tag}>" for c in row)
                        + "</tr>"
                    )
                out.append("</tbody></table>")
            continue
        if s.startswith("#### "):
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f"<h5>{_inline_md(s[5:])}</h5>")
        elif s.startswith("> "):
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f'<blockquote class="md-quote">{_inline_md(s[2:])}</blockquote>')
        elif s.startswith("### "):
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f"<h4>{_inline_md(s[4:])}</h4>")
        elif s.startswith("## "):
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f"<h3>{_inline_md(s[3:])}</h3>")
        elif s.startswith("# "):
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f"<h3>{_inline_md(s[2:])}</h3>")
        elif s.startswith("- ") or s.startswith("* "):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{_inline_md(s[2:])}</li>")
        elif s == "---":
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append("<hr>")
        else:
            if in_ul:
                out.append("</ul>")
                in_ul = False
            out.append(f"<p>{_inline_md(s)}</p>")
        i += 1
    if in_ul:
        out.append("</ul>")
    return "\n".join(out)


def load_ai_coaching_body() -> str | None:
    if not os.path.exists(COACH_MD):
        return None
    with open(COACH_MD, encoding="utf-8") as f:
        content = f.read()
    marker = "## コーチングレビュー"
    if marker in content:
        body = content.split(marker, 1)[1].strip()
    else:
        body = content.strip()
    if not body:
        return None
    return md_to_html(body)


def _ai_coaching_title() -> str:
    meta = format_last_coach_meta()
    if meta and meta.get("model"):
        return f'🤖 AI 月次コーチング（{html_module.escape(meta["model"])}）'
    return "🤖 AI 月次コーチング（Claude）"


def build_ai_coaching_section() -> str:
    title = _ai_coaching_title()
    body = load_ai_coaching_body()
    if body:
        return (
            '\n  <div class="section ai-coach-section" id="ai-coaching">\n'
            f'    <h2>{title}</h2>\n'
            '    <div class="ai-coach-body">' + body + '</div>\n'
            '  </div>'
        )
    return (
        '\n  <div class="section ai-coach-section ai-coach-empty" id="ai-coaching">\n'
        f'    <h2>{title}</h2>\n'
        '    <p class="ai-coach-placeholder">'
        '月間総評・翌月提案は GitHub Actions の毎日更新後、または coach_claude.py 実行後にここに表示されます。</p>\n'
        '  </div>'
    )


def build_ai_next_month_plan_section() -> str:
    """前月 AI レビューの「当月練習提案」を表示（当月 HTML 用）。"""
    plan_md = load_next_month_plan_markdown(TARGET_YEAR, TARGET_MONTH)
    if not plan_md:
        return ""
    src = prev_month_label(TARGET_YEAR, TARGET_MONTH)
    body = md_to_html(plan_md)
    ai_week = resolve_ai_weekly_plan(TARGET_YEAR, TARGET_MONTH, date.today())
    week_note = ""
    if ai_week:
        week_note = (
            f'<p class="ai-plan-week-note">📅 今週の日次メニューは下の「今週の練習メニュー」に '
            f'<strong>{html_module.escape(ai_week["week_title"])}</strong> として反映済みです。</p>'
        )
    return (
        '\n  <div class="section ai-plan-section" id="ai-month-plan">\n'
        f'    <h2>🤖 {html_module.escape(src)} AIコーチからの{MONTH_LABEL}練習提案</h2>\n'
        f'    {week_note}'
        '    <div class="ai-coach-body ai-plan-body">' + body + '</div>\n'
        '  </div>'
    )

# ── HTML 生成（4タブ・モバイルダッシュボード v2） ───────────────────────────
hh, rem = divmod(total_sec, 3600); mm = rem // 60

_today_now = date.today()
_is_current_month = (TARGET_YEAR == _today_now.year and TARGET_MONTH == _today_now.month)
_DOW_JP = ["月", "火", "水", "木", "金", "土", "日"]


def _fmt_dist_km(d):
    d = float(d or 0)
    if d <= 0:
        return "—"
    if abs(d - round(d)) < 1e-9:
        return f"{int(round(d))}km"
    return f"{d:g}km"


def _plan_zone_code(type_key, label=""):
    m = {"rest": "休養", "easy": "E", "tempo": "T", "interval": "I",
         "long": "ロングE", "race": "レース", "move": "移動"}
    z = m.get((type_key or "").lower())
    if z:
        return z
    lab = label or ""
    if "休" in lab: return "休養"
    if "レース" in lab: return "レース"
    if "移動" in lab: return "移動"
    if "ロング" in lab: return "ロングE"
    if "テンポ" in lab: return "T"
    if "インターバル" in lab: return "I"
    return "E"


# VDOT → [eLo, eHi, M, T, I, R] 秒/km（build_performance_profile の T テーブルを移植）
_VDOT_PACE_TABLE = {
    45:[349,374,310,300,269,254], 47:[339,363,301,290,261,246],
    49:[329,353,293,282,253,238], 50:[324,347,289,278,249,234],
    51:[319,342,286,274,246,231], 52:[314,337,282,269,242,228],
    53:[309,332,278,265,238,224], 54:[304,326,274,261,234,220],
    55:[300,322,271,257,231,217], 56:[296,317,267,254,227,213],
    57:[293,314,264,250,224,210], 58:[290,311,261,247,222,208],
    59:[287,308,258,244,219,205], 60:[284,305,255,241,216,202],
    61:[281,302,252,238,213,199], 62:[278,299,249,235,210,197],
    63:[275,296,246,232,208,194], 64:[272,293,243,230,205,192],
    65:[270,290,241,227,203,189], 67:[264,284,236,222,198,185],
    70:[257,276,229,216,192,179],
}


def _vdot_paces(v):
    keys = sorted(_VDOT_PACE_TABLE)
    if v <= keys[0]:
        return _VDOT_PACE_TABLE[keys[0]]
    if v >= keys[-1]:
        return _VDOT_PACE_TABLE[keys[-1]]
    for i in range(len(keys) - 1):
        k1, k2 = keys[i], keys[i + 1]
        if k1 <= v <= k2:
            r = (v - k1) / (k2 - k1)
            return [round(a + (_VDOT_PACE_TABLE[k2][j] - a) * r)
                    for j, a in enumerate(_VDOT_PACE_TABLE[k1])]
    return _VDOT_PACE_TABLE[keys[-1]]


def _pace_str(s):
    return f"{s // 60}:{s % 60:02d}"


# ── 今週の日次メニュー（AI プラン優先・なければ固定プラン） ──────────────────
def _week_days_from_plan():
    if not _is_current_month:
        return [], "", "", 0.0, None
    mon = _today_now - timedelta(days=_today_now.weekday())
    ai_week = resolve_ai_weekly_plan(TARGET_YEAR, TARGET_MONTH, _today_now)
    plan_by_date = (ai_week or {}).get("plan_by_date") or {}
    fixed = {wd: (wd, tk, lb, dk, ds) for (wd, tk, lb, dk, ds) in _WEEKLY_PLAN}
    days, total = [], 0.0
    for i in range(7):
        d = mon + timedelta(days=i)
        if plan_by_date and d in plan_by_date:
            _, tk, lb, dk, ds = plan_by_date[d]
        elif plan_by_date:
            tk, lb, dk, ds = "rest", "—", 0.0, "AI提案の対象週外"
        else:
            _, tk, lb, dk, ds = fixed.get(i, (i, "rest", "休養", 0.0, "完全休養"))
        dk = float(dk or 0)
        total += dk
        days.append({
            "id": "d" + d.strftime("%m%d"),
            "date": f"{d.month}/{d.day}",
            "dow": _DOW_JP[d.weekday()],
            "zone": _plan_zone_code(tk, lb),
            "dist": _fmt_dist_km(dk),
            "distKm": round(dk, 1),
            "desc": ds or "",
        })
    if ai_week:
        return days, ai_week.get("subtitle", ""), ai_week.get("week_title", ""), total, ai_week
    return days, "固定プラン（前月AIレビュー未生成）", "", total, None


def _parse_dist_num(dist_str):
    """"8km" / "13〜15km" / "—" → 数値（範囲は中央値、休養は0）。"""
    s = (dist_str or "").replace("km", "").strip()
    if not s or s in ("—", "-", "－"):
        return 0.0
    nums = [float(x) for x in re.findall(r"[\d.]+", s)]
    if not nums:
        return 0.0
    if ("〜" in s or "-" in s) and len(nums) >= 2:
        return round((nums[0] + nums[1]) / 2, 1)
    return nums[0]


def _fmt_week_target(tk):
    if tk is None or tk == "":
        return "—"
    s = str(tk).strip()
    return s if "km" in s else s + "km"


def _payload_day_from_raw(d):
    """{date,dow,zone,dist,desc} → プランタブ描画用（id/distKm 付与）。"""
    dd = (d.get("date") or "").strip()
    did = "d0000"
    m = re.match(r"(\d+)/(\d+)", dd)
    if m:
        did = f"d{int(m.group(1)):02d}{int(m.group(2)):02d}"
    dist = (d.get("dist") or "—").strip() or "—"
    return {
        "id": did, "date": dd, "dow": d.get("dow", ""),
        "zone": d.get("zone") or "E", "dist": dist,
        "distKm": _parse_dist_num(dist), "desc": d.get("desc", ""),
    }


def _range_contains_today(rng):
    m = re.match(r"(\d+)/(\d+)〜(\d+)/(\d+)", rng or "")
    if not m:
        return False
    sm, sd, em, ed = (int(x) for x in m.groups())
    try:
        return date(TARGET_YEAR, sm, sd) <= _today_now <= date(TARGET_YEAR, em, ed)
    except ValueError:
        return False


def _load_full_month_weeks():
    """plan_<YYYYMM>.json → 前月MD全週パース の順で全週を取得。無ければ None。"""
    path = f"plan_{YYYYMM}.json"
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            weeks = data.get("weeks")
            if isinstance(weeks, list) and weeks:
                return weeks
        except (OSError, ValueError):
            pass
    try:
        from coach_common import parse_all_weeks_from_md
        weeks = parse_all_weeks_from_md(TARGET_YEAR, TARGET_MONTH)
        if weeks:
            return weeks
    except Exception:
        pass
    return None


def build_plan_payload():
    note = "月間プランは前月AIレビュー生成時に更新されます"
    full_weeks = _load_full_month_weeks()

    if full_weeks:
        # ①plan_JSON / ②MD全週パース：5週フル表示
        months, week_targets, cur_num, cur_days = [], {}, 0, []
        for w in full_weeks:
            num = w.get("num") or (len(months) + 1)
            rng = w.get("range", "")
            pdays = [_payload_day_from_raw(d) for d in w.get("days", [])]
            wtotal = round(sum(d["distKm"] for d in pdays), 1)
            week_targets[num] = _parse_dist_num(str(w.get("target_km", ""))) or wtotal
            is_cur = _is_current_month and _range_contains_today(rng)
            if is_cur:
                cur_num, cur_days = num, pdays
            months.append({
                "num": num, "numLabel": f"第{num}週", "range": rng,
                "theme": (w.get("theme") or f"第{num}週")[:40],
                "target": _fmt_week_target(w.get("target_km")),
                "days": pdays, "current": is_cur,
            })
        title = ""
        if cur_num:
            cm = next((mo for mo in months if mo["num"] == cur_num), None)
            if cm:
                title = f"今週：{cm['numLabel']}（{cm['range']}）"
        return {
            "currentWeek": cur_num, "title": title,
            "subtitle": "前月 AI コーチの月間プランに基づく",
            "targetKm": round(sum(d["distKm"] for d in cur_days), 1),
            "days": cur_days, "months": months,
            "weekTargets": week_targets, "note": note,
        }

    # ③resolve_ai_weekly_plan（今週のみ）/ ④固定プラン（従来フォールバック）
    days, subtitle, week_title, total, ai_week = _week_days_from_plan()
    if _is_current_month:
        current_week = (_today_now - MONTH_START).days // 7 + 1
        mon = _today_now - timedelta(days=_today_now.weekday())
        sun = mon + timedelta(days=6)
        title = week_title or f"今週：第{current_week}週（{mon.month}/{mon.day}〜{sun.month}/{sun.day}）"
        months = []
        if days:
            months.append({
                "num": current_week, "numLabel": f"第{current_week}週",
                "range": f"{mon.month}/{mon.day}〜{sun.month}/{sun.day}",
                "theme": (subtitle or f"第{current_week}週")[:40],
                "target": _fmt_dist_km(total), "days": days, "current": True,
            })
        week_targets = {current_week: round(total, 1)} if days else {}
    else:
        current_week, title, months, week_targets = 0, "", [], {}
    return {
        "currentWeek": current_week, "title": title, "subtitle": subtitle,
        "targetKm": round(total, 1), "days": days, "months": months,
        "weekTargets": week_targets, "note": note,
    }


# ── アクティビティ詳細（ラップ表 + コーチ評価。地図は展開時に遅延初期化） ─────
def build_activity_detail(r, lp, coach):
    aid = str(r.get("activity_id"))
    sc = None
    breakdown = None
    try:
        sc, breakdown = score_run(r, lp, coach.get("run_type", "easy"))
    except Exception:
        sc, breakdown = None, None
    bd_html = ""
    if breakdown:
        bd_html = (
            '<div class="rr-bd">'
            f'<span>ペース {breakdown["pace"]:.1f}</span>'
            f'<span>心拍 {breakdown["hr"]:.1f}</span>'
            f'<span>一貫性 {breakdown["cons"]:.1f}</span>'
            f'<span>バランス {breakdown["bal"]:.1f}</span>'
            '</div>'
        )
    tips_html = "".join(f"<li>{t}</li>" for t in (coach.get("tips") or []))
    tips_block = f'<ul class="rr-tips">{tips_html}</ul>' if tips_html else ""
    lap_html = ""
    if len(lp) > 1:
        rows = ""
        for lap in lp:
            hr = lap.get("avg_heartrate") or "-"
            rows += (
                "<tr>"
                f"<td>{lap.get('lap_index', '')}</td>"
                f"<td>{float(lap.get('distance_km') or 0):.2f}</td>"
                f"<td>{lap.get('moving_time', '-')}</td>"
                f"<td>{lap.get('pace_per_km', '-')}</td>"
                f"<td>{hr}</td>"
                "</tr>"
            )
        lap_html = (
            '<table class="rr-laptable"><thead><tr>'
            '<th>Lap</th><th>距離</th><th>時間</th><th>ペース</th><th>HR</th>'
            f'</tr></thead><tbody>{rows}</tbody></table>'
        )
    map_block = f'<div id="rrmap_{aid}" class="rr-map"></div>' if gps_map.get(aid) else ''
    detail = (
        '<div class="rr-detail-inner">'
        + bd_html
        + f'<div class="rr-cp"><strong>🎯 目的：</strong>{coach.get("purpose", "")}</div>'
        + f'<div class="rr-cp"><strong>📊 評価：</strong>{coach.get("assessment", "")}</div>'
        + tips_block + map_block + lap_html
        + '</div>'
    )
    return detail, sc


def build_activities_payload():
    acts = []
    for r in sorted(runs, key=lambda x: x.get("date", ""), reverse=True):
        aid = str(r.get("activity_id"))
        dist = float(r.get("distance_km") or 0)
        lp = laps_by_id.get(aid, [])
        typ, col = training_type(r)
        try:
            coach = coaching_comment(r, lp)
        except Exception:
            coach = {"run_type": "easy", "purpose": "", "assessment": "", "tips": [], "color": col}
        detail, sc = build_activity_detail(r, lp, coach)
        score = sc if (dist >= 1.5 and sc is not None) else None
        try:
            d = date.fromisoformat(r["date"])
            dow, dlabel = _DOW_JP[d.weekday()], f"{d.month}/{d.day}"
        except Exception:
            dow, dlabel = r.get("weekday", ""), (r.get("date") or "")[5:]
        coords = gps_map.get(aid)
        acts.append({
            "id": aid,
            "date": dlabel,
            "dow": dow,
            "typeLabel": typ,
            "name": r.get("name", ""),
            "dist": f"{dist:.1f}km",
            "pace": r.get("pace_per_km") or "-",
            "hr": r.get("avg_heartrate") or "-",
            "score": score,
            "detailHtml": detail,
            "mapId": f"rrmap_{aid}",
            "mapCoords": coords if coords else None,
            "mapColor": coach.get("color", col),
        })
    return acts


def build_today_payload(plan_payload):
    show_garmin = (REPORT_EDITION == "local")
    goal = _MONTH_GOAL
    pct = min(100, round(total_dist / goal * 100)) if goal else 0
    month_summary = {
        "dist": round(total_dist, 1), "goal": goal, "pct": pct,
        "runCount": len(runs), "totalTime": f"{hh}:{mm:02d}",
        "avgHr": round(avg_hr) if avg_hr else None, "elev": round(total_elev),
    }
    weeks = (MONTH_END - MONTH_START).days // 7 + 1
    week_targets = plan_payload.get("weekTargets") or {}
    week_bars = []
    for w in range(1, weeks + 1):
        actual = round(by_week[w]["dist"], 1) if w in by_week else 0
        # weekTargets のキーは JSON 化で文字列になる場合があるため両対応
        plan_t = week_targets.get(w) or week_targets.get(str(w))
        week_bars.append({"label": f"第{w}週", "actual": actual, "plan": plan_t or None})

    menu = None
    if _is_current_month and plan_payload["days"]:
        tgt = f"{_today_now.month}/{_today_now.day}"
        for d in plan_payload["days"]:
            if d["date"] == tgt:
                menu = {"zone": d["zone"], "dist": d["dist"], "desc": d["desc"],
                        "dateLabel": f"{_today_now.month}/{_today_now.day}（{_DOW_JP[_today_now.weekday()]}）"}
                break

    race = None
    if _is_current_month and _NEXT_RACE:
        dtr = (_NEXT_RACE - _today_now).days
        if dtr is not None and dtr >= 0:
            race = {"name": _RACE_NAME,
                    "sub": f"{_NEXT_RACE.month}/{_NEXT_RACE.day}（{_DOW_JP[_NEXT_RACE.weekday()]}）・{_RACE_DIST:g}km",
                    "days": dtr}

    garmin_days = None
    garmin_labels = None
    condition_alert = None
    vo2 = None
    if show_garmin:
        try:
            import garmin as _gm2
            vo2 = _gm2.latest_vo2max()
            recent = _gm2.recent_daily(6, TARGET_YEAR, TARGET_MONTH)
            gd = []
            for g in recent:
                ds = (g.get("date") or "")[5:]
                sh = (g.get("sleep_hours") or "").strip()
                gd.append({
                    "date": ds.replace("-", "/") if ds else "",
                    "ready": (g.get("readiness_score") or "").strip() or "—",
                    "hv": (g.get("hrv_last_night") or "").strip() or "—",
                    "sleep": (sh + "h") if sh else "—",
                    "rhr": (g.get("resting_hr") or "").strip() or "—",
                    "status": (g.get("training_status") or "").strip() or "—",
                })
            if gd:
                garmin_days = gd
                garmin_labels = {"hv": "HRV"}
            if _is_current_month and recent:
                latest = recent[0]
                try:
                    rd = int(float(latest.get("readiness_score") or 0))
                except (TypeError, ValueError):
                    rd = None
                if rd is not None and rd < 20:
                    menu_txt = f"予定は「{menu['zone']} {menu['dist']}」ですが、" if menu else ""
                    sl = (latest.get("sleep_hours") or "").strip()
                    st = (latest.get("training_status") or "").strip()
                    parts = [f"レディネス <strong>{rd}/100</strong>"]
                    if sl:
                        parts.append(f"昨夜の睡眠 <strong>{sl}h</strong>")
                    if st:
                        parts.append(f"ステータス {st}")
                    condition_alert = {
                        "title": "今日は回復を最優先",
                        "body": "・".join(parts) + "。" + menu_txt
                                + "この状態では完全休養か30分ウォークへの置き換えを推奨します。",
                    }
        except Exception:
            garmin_days, garmin_labels, condition_alert, vo2 = None, None, None, None

    return {
        "conditionAlert": condition_alert,
        "menu": menu,
        "raceCountdown": race,
        "monthSummary": month_summary,
        "weekBars": week_bars,
        "showGarmin": bool(show_garmin and garmin_days),
        "garmin": garmin_days,
        "garminLabels": garmin_labels,
        "vo2max": (f"{vo2:.1f}" if isinstance(vo2, (int, float)) else None),
        "activities": build_activities_payload(),
    }


def _extract_md_highlights(md, n=5):
    items = []
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("- ") or s.startswith("* "):
            txt = re.sub(r"\*\*(.+?)\*\*", r"\1", s[2:]).strip()
            if txt:
                items.append(txt)
        if len(items) >= n:
            break
    return items


def build_coach_payload():
    body_html = load_ai_coaching_body()
    raw = ""
    if os.path.exists(COACH_MD):
        try:
            with open(COACH_MD, encoding="utf-8") as f:
                raw = f.read()
        except OSError:
            raw = ""
    meta = format_last_coach_meta()
    model = (meta or {}).get("model", "Claude")
    kicker = f"AI 月次コーチング ・ {MONTH_LABEL}レビュー"
    if body_html:
        headline = f"{MONTH_LABEL}のトレーニング総評"
        for line in raw.splitlines():
            s = re.sub(r"\*\*(.+?)\*\*", r"\1", line.strip().lstrip("#").strip())
            if s and not s.startswith("|") and len(s) > 6 and "コーチングレビュー" not in s:
                headline = s[:80]
                break
        points = [{"sym": "•", "color": "#D6D3CE", "text": t}
                  for t in _extract_md_highlights(raw, 3)]
    else:
        headline = "AIレビュー未生成 — ヘッダーの「AI」ボタンで生成できます"
        points = []
    verdict = {"kicker": kicker, "headline": headline, "points": points}

    sections = []
    if body_html:
        sections.append({"id": "review", "title": "月次レビュー", "meta": model,
                         "body": body_html, "open": True})
    for r in sorted(runs, key=lambda x: x.get("date", ""), reverse=True):
        if float(r.get("distance_km") or 0) < 1.5:
            continue
        aid = str(r.get("activity_id"))
        lp = laps_by_id.get(aid, [])
        try:
            c = coaching_comment(r, lp)
        except Exception:
            continue
        body = (f'<div class="rr-cp"><strong>🎯 目的：</strong>{c.get("purpose", "")}</div>'
                f'<div class="rr-cp"><strong>📊 評価：</strong>{c.get("assessment", "")}</div>')
        sections.append({
            "id": "a" + aid,
            "title": f'{r.get("date", "")} {r.get("name", "")}',
            "meta": f'{float(r.get("distance_km") or 0):.1f}km ・ {r.get("pace_per_km", "-")}/km',
            "body": body, "open": False,
        })

    keypoints = _extract_md_highlights(raw, 5) if raw else []

    vdot = _VO2MAX
    p = _vdot_paces(vdot)
    hrmax = 0
    for r in runs:
        try:
            hrmax = max(hrmax, int(float(r.get("max_heartrate") or 0)))
        except (TypeError, ValueError):
            pass
    if hrmax < 150:
        hrmax = 198

    def _hrr(lo, hi):
        return f"{int(hrmax * lo)}〜{int(hrmax * hi)}bpm"

    pace_zones = [
        {"zone": "E イージー", "pace": f"{_pace_str(p[0])}〜{_pace_str(p[1])}",
         "desc": f"{_hrr(.62, .75)} ・ 有酸素基礎・回復", "color": "#22c55e"},
        {"zone": "M マラソン", "pace": _pace_str(p[2]),
         "desc": f"{_hrr(.79, .87)} ・ レースペース", "color": "#3b82f6"},
        {"zone": "T テンポ", "pace": f"{_pace_str(p[3])}〜{_pace_str(p[3] + 6)}",
         "desc": f"{_hrr(.85, .90)} ・ 乳酸閾値向上", "color": "#f59e0b"},
        {"zone": "I インターバル", "pace": _pace_str(p[4]),
         "desc": f"{_hrr(.95, 1.0)} ・ VO₂max 向上", "color": "#ef4444"},
        {"zone": "R レペ", "pace": _pace_str(p[5]),
         "desc": "最大強度 ・ スピード・走力", "color": "#8b5cf6"},
    ]
    return {"verdict": verdict, "sections": sections, "keypoints": keypoints,
            "paceZones": pace_zones, "vdot": vdot}


def build_records_payload():
    import math as _m

    def _sec_to_vdot(sec):
        v = 42195 / sec * 60
        vo2 = -4.6 + 0.182258 * v + 0.000104 * v ** 2
        t = sec / 60
        pctf = 0.8 + 0.1894393 * _m.exp(-0.012778 * t) + 0.2989558 * _m.exp(-0.1932605 * t)
        return round(vo2 / pctf, 1)

    pbs = load_pbs()
    keys = ["1mile", "3km", "5km", "10km", "half", "full"]
    cards, gaps = [], {}
    for k in keys:
        meta = _PB_META[k]
        pb = pbs.get(k, {})
        t310 = _TARGETS[k]["sub310"]
        t300 = _TARGETS[k]["sub300"]
        pb_sec = pb.get("time_sec")
        if pb_sec:
            reach310 = pb_sec <= t310
            bar_pct = max(0, min(100, int((t310 - pb_sec) / max(t310 - t300, 1) * 100)))
            if reach310:
                badge, ok = "✓ Sub 3:10", True
            else:
                gm, gs = divmod(pb_sec - t310, 60)
                badge, ok = f"あと -{gm}:{gs:02d}", False
            gaps[k] = pb_sec - t310
        else:
            bar_pct, badge, ok = 0, "未計測", False
        cards.append({"dist": meta["label"].upper(), "time": pb.get("time_str", "—"),
                      "date": pb.get("date", ""), "pct": bar_pct, "ok": ok,
                      "badge": badge, "target": _sec_to_str(t310)})
    focus = None
    if gaps:
        worst = max(gaps, key=lambda k: gaps[k])
        if gaps[worst] > 0:
            fm, fs = divmod(gaps[worst], 60)
            focus = f"{_PB_META[worst]['label']}（Sub 3:10 まであと {fm}:{fs:02d}）"

    vdot_cur = _VO2MAX
    pb_vdot = _sec_to_vdot(_CURRENT_PB_SEC)
    marker = max(0, min(100, (vdot_cur - 48) / (63 - 48) * 100))
    to_g1 = max(0, 59 - vdot_cur)
    to_ult = max(0, 63 - vdot_cur)
    return {
        "pbs": cards, "focus": focus,
        "vdot": {"current": vdot_cur, "markerPct": round(marker, 1),
                 "pbVdot": round(pb_vdot), "pbStr": _CURRENT_PB},
        "targets": [
            {"kicker": "NEXT GOAL", "value": "Sub 3:10", "color": "#B45309",
             "sub": f"VDOT 59 相当 ・ あと +{to_g1}"},
            {"kicker": "ULTIMATE", "value": "Sub 3:00", "color": "#B91C1C",
             "sub": f"VDOT 63 相当 ・ あと +{to_ult}"},
        ],
    }


_plan_payload = build_plan_payload()
payload = {
    "meta": {
        "monthLabel": MONTH_LABEL, "yyyymm": YYYYMM, "edition": REPORT_EDITION,
        "isCurrentMonth": _is_current_month, "monthGoal": _MONTH_GOAL,
    },
    "today": build_today_payload(_plan_payload),
    "plan": _plan_payload,
    "coach": build_coach_payload(),
    "records": build_records_payload(),
}

# プライバシー: online 版（公開）は Garmin 生データ（レディネス/HRV/睡眠/安静時HR）を一切埋め込まない
if REPORT_EDITION != "local":
    payload["today"]["garmin"] = None
    payload["today"]["garminLabels"] = None
    payload["today"]["conditionAlert"] = None
    payload["today"]["showGarmin"] = False
    payload["today"]["vo2max"] = None


CSS = """
  html,body{margin:0;padding:0;background:#F4F3F0}
  *{box-sizing:border-box}
  body{font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans","Segoe UI",sans-serif;color:#1C1917;-webkit-font-smoothing:antialiased;line-height:1.6}
  a{color:#C2410C;text-decoration:none}
  a:hover{color:#FC4C02;text-decoration:underline}
  .edition-banner{padding:8px 16px;font-size:12.5px;line-height:1.5}
  .edition-banner a{color:inherit;font-weight:700}
  .edition-banner.edition-online{background:#dbeafe;color:#1e3a8a}
  .edition-banner.edition-local{background:#ffedd5;color:#9a3412}
  .edition-banner.edition-other{background:#fef3c7;color:#92400e}
  .rr-header{position:sticky;top:0;z-index:50;background:rgba(255,255,255,.94);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);border-bottom:1px solid #E7E5E1}
  .rr-header-inner{max-width:760px;margin:0 auto;padding:0 16px}
  .rr-header-row1{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:12px 0 8px;flex-wrap:wrap;transition:padding .15s ease}
  .rr-title{display:flex;align-items:center;gap:10px}
  .rr-bar{width:10px;height:22px;background:#FC4C02;border-radius:3px;transition:height .15s ease}
  .rr-month{font-size:19px;font-weight:800;letter-spacing:-.01em;transition:font-size .15s ease}
  .rr-sub{font-size:12px;color:#78716C;font-weight:600}
  /* スクロール時の折りたたみ（モバイルでヘッダー面積を圧縮） */
  .rr-header.rr-collapsed .rr-header-row1{padding:6px 0}
  .rr-header.rr-collapsed .rr-month{font-size:15px}
  .rr-header.rr-collapsed .rr-bar{height:16px}
  .rr-header.rr-collapsed .rr-sub,
  .rr-header.rr-collapsed .header-actions,
  .rr-header.rr-collapsed #github-sync-panel,
  .rr-header.rr-collapsed .month-nav,
  .rr-header.rr-collapsed .rr-meta{display:none}
  .rr-tabs{display:flex;gap:2px;overflow-x:auto}
  .rr-tab{all:unset;box-sizing:border-box;cursor:pointer;padding:9px 16px 11px;font-size:13.5px;font-weight:700;color:#78716C;white-space:nowrap;border-bottom:3px solid transparent}
  .rr-main{max-width:760px;margin:0 auto;padding:20px 16px 80px}
  .rr-main section{display:flex;flex-direction:column;gap:16px}
  .header-actions{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
  .btn-update{background:#FC4C02;color:#fff;border:none;border-radius:999px;padding:7px 14px;font-size:12px;font-weight:700;cursor:pointer;white-space:nowrap;transition:transform .15s,opacity .15s}
  .btn-update:hover:not(:disabled){transform:translateY(-1px)}
  .btn-update:disabled{opacity:.65;cursor:wait}
  .btn-coach{background:#eef2ff;color:#4338ca;border:none;border-radius:999px;padding:7px 14px;font-size:12px;font-weight:700;cursor:pointer;white-space:nowrap;transition:transform .15s,opacity .15s}
  .btn-coach:hover:not(:disabled){transform:translateY(-1px)}
  .btn-coach:disabled{opacity:.65;cursor:wait}
  .btn-garmin{background:#ecfdf5;color:#047857;border:none;border-radius:999px;padding:7px 12px;font-size:12px;font-weight:700;cursor:pointer;transition:transform .1s}
  .btn-garmin:hover:not(:disabled){transform:translateY(-1px)}
  .btn-garmin:disabled{opacity:.65;cursor:wait}
  .btn-sync-link{display:inline-flex;align-items:center;text-decoration:none;background:#FC4C02;color:#fff;border-radius:999px;padding:7px 14px;font-size:12px;font-weight:700;white-space:nowrap}
  #github-sync-panel{display:none}
  .month-nav{display:flex;align-items:center;gap:4px;flex-wrap:wrap}
  .mnav-arrow{border:1px solid #E7E5E1;background:#fff;color:#78716C;border-radius:8px;padding:5px 10px;font-size:12px;font-weight:700;text-decoration:none}
  .mnav-disabled{color:#D6D3CE}
  .mnav-label{font-size:12px;font-weight:700;color:#1C1917;padding:0 4px;white-space:nowrap}
  .back-latest{font-size:11px;color:#C2410C;font-weight:700;text-decoration:none;margin-left:6px}
  .update-hint{font-size:11px;color:#78716C;max-width:200px;line-height:1.4}
  .rr-meta{font-size:10.5px;color:#78716C;line-height:1.45;padding-bottom:6px}
  .rr-meta p{margin:0}
  .last-coach.stale{color:#B45309}
  .last-coach-warn{color:#B45309;font-weight:700}
  .update-status{margin:0 0 8px;background:#FEF6E7;border-radius:10px;padding:8px 12px;font-size:12px;line-height:1.5;display:none;color:#7c2d12}
  .update-status.visible{display:block}
  .update-status.error{background:#FEF2F2;color:#991B1B}
  .update-status.success{background:#ECFDF3;color:#15803D}
  .update-log{margin-top:6px;max-height:120px;overflow:auto;font-family:ui-monospace,monospace;font-size:11px;white-space:pre-wrap}
  .rr-map{height:200px;border-radius:10px;margin:10px 0;background:#EDECE8}
  .rr-bd{display:flex;gap:8px;flex-wrap:wrap;font-size:11px;color:#78716C;margin-bottom:8px}
  .rr-bd span{background:#FAFAF8;border-radius:6px;padding:2px 8px}
  .rr-cp{font-size:12.5px;color:#44403C;line-height:1.7;margin-bottom:6px}
  .rr-tips{margin:6px 0 6px 18px;font-size:12px;color:#57534E;line-height:1.7}
  .rr-laptable{width:100%;border-collapse:collapse;font-size:11.5px;margin-top:8px}
  .rr-laptable th,.rr-laptable td{border:1px solid #EDECE8;padding:4px 8px;text-align:right}
  .rr-laptable th{background:#FAFAF8;color:#78716C;font-weight:700}
  .rr-coachbody :first-child{margin-top:0}
  .rr-coachbody table{border-collapse:collapse;font-size:12px}
  .rr-coachbody th,.rr-coachbody td{border:1px solid #EDECE8;padding:4px 8px}
"""


# ── レンダリング + 状態管理（プレーン文字列。f-string ではないので { } はそのまま） ──
RENDER_JS = r"""
const DATA = /*__DATA__*/;
(function(){
  const M = DATA.meta, T = DATA.today, P = DATA.plan, C = DATA.coach, R = DATA.records;
  const DONE_KEY = 'rr2-done-' + M.yyyymm;
  const TABS = ['today','plan','coach','records'];
  const state = { tab:'today', openWeek: P.currentWeek||0, openCoach:{}, done:{} };
  try { state.done = JSON.parse(localStorage.getItem(DONE_KEY) || '{}') || {}; } catch(e){}
  const mapInited = {};

  function esc(s){ return String(s==null?'':s).replace(/[&<>]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c]; }); }
  function zoneStyle(zone){
    const map = {'E':'#22c55e','E+坂':'#16a34a','ロングE':'#15803d','T':'#f59e0b','M':'#3b82f6','I':'#ef4444','R':'#8b5cf6','休養':'#A8A29E','レース':'#FC4C02','移動':'#A8A29E'};
    const bg = map[zone] || '#A8A29E';
    return 'background:'+bg+';color:#fff;font-size:11px;font-weight:800;border-radius:6px;padding:3px 0;width:58px;text-align:center;flex-shrink:0';
  }
  function chipStyle(label){
    const m = {'イージー走':['#ECFDF3','#15803D','#BBF7D0'],'テンポ走':['#FEF6E7','#B45309','#FDE8C0'],'インターバル':['#FEF2F2','#B91C1C','#FECACA'],'ロング走':['#EEF2FF','#4338CA','#C7D2FE']};
    const c = m[label] || ['#F5F5F4','#57534E','#E7E5E1'];
    return 'background:'+c[0]+';color:'+c[1]+';border:1px solid '+c[2]+';font-size:11px;font-weight:800;border-radius:999px;padding:3px 10px;flex-shrink:0';
  }
  function tile(label,val,unit){
    const u = unit ? "<span style='font-size:12px;color:#A8A29E'> "+esc(unit)+"</span>" : '';
    return "<div style='background:#FAFAF8;border-radius:10px;padding:10px 12px'><div style='font-size:11px;color:#78716C;font-weight:700;margin-bottom:2px'>"+esc(label)+"</div><div style='font-size:18px;font-weight:800'>"+esc(val)+u+"</div></div>";
  }

  // ---------- 今日 ----------
  function renderToday(){
    const el = document.getElementById('tab-today');
    let h = '';
    if (T.conditionAlert){
      h += "<div style='background:#FEF2F2;border:1px solid #FECACA;border-radius:14px;padding:16px 18px;display:flex;gap:14px;align-items:flex-start'><div style='font-size:22px;line-height:1.2'>🛑</div><div style='flex:1'><div style='font-size:14px;font-weight:800;color:#991B1B;margin-bottom:2px'>"+esc(T.conditionAlert.title)+"</div><div style='font-size:12.5px;color:#7F1D1D;line-height:1.7'>"+T.conditionAlert.body+"</div></div></div>";
    }
    if (T.menu){
      h += "<div style='background:#fff;border:1px solid #E7E5E1;border-radius:14px;padding:18px'><div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:12px'><div style='font-size:12px;font-weight:800;color:#78716C;letter-spacing:.08em'>今日のメニュー — "+esc(T.menu.dateLabel)+"</div><a href='#plan' data-goto='plan' style='font-size:12px;font-weight:700;color:#C2410C'>今週のプラン →</a></div><div style='display:flex;align-items:center;gap:14px;flex-wrap:wrap'><span style='"+zoneStyle(T.menu.zone)+";width:auto;padding:6px 12px;font-size:13px'>"+esc(T.menu.zone)+"</span><div style='font-size:26px;font-weight:900;letter-spacing:-.02em'>"+esc(T.menu.dist)+"</div><div style='font-size:13px;color:#57534E'>"+esc(T.menu.desc)+"</div></div></div>";
    }
    if (T.raceCountdown){
      const r = T.raceCountdown;
      h += "<div style='background:#1C1917;color:#fff;border-radius:14px;padding:16px 18px;display:flex;align-items:center;gap:16px'><div style='font-size:24px'>🏔️</div><div style='flex:1'><div style='font-size:14px;font-weight:800'>"+esc(r.name)+"</div><div style='font-size:12px;color:#A8A29E'>"+esc(r.sub)+"</div></div><div style='text-align:right'><div style='font-size:26px;font-weight:900;color:#FC4C02;line-height:1'>"+r.days+"</div><div style='font-size:11px;color:#A8A29E;font-weight:700'>日後</div></div></div>";
    }
    const ms = T.monthSummary;
    const monLabel = M.monthLabel.replace(/^\d+年/,'');
    h += "<div style='background:#fff;border:1px solid #E7E5E1;border-radius:14px;padding:18px'><div style='display:flex;justify-content:space-between;align-items:baseline;margin-bottom:10px'><div style='font-size:12px;font-weight:800;color:#78716C;letter-spacing:.08em'>"+esc(monLabel)+"の走行距離</div><div style='font-size:12px;color:#78716C'>月間目標 "+ms.goal+"km</div></div><div style='display:flex;align-items:baseline;gap:8px;margin-bottom:10px'><div style='font-size:34px;font-weight:900;letter-spacing:-.02em'>"+ms.dist+"</div><div style='font-size:14px;font-weight:700;color:#78716C'>km</div><div style='font-size:12px;color:#A8A29E;margin-left:auto'>"+ms.pct+"%</div></div><div style='height:8px;background:#EDECE8;border-radius:4px;overflow:hidden;margin-bottom:16px'><div style='height:8px;width:"+ms.pct+"%;background:#FC4C02;border-radius:4px'></div></div><div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px'>"+tile('ラン回数',ms.runCount,'回')+tile('総時間',ms.totalTime,'')+tile('平均心拍',ms.avgHr==null?'—':ms.avgHr,'bpm')+tile('獲得標高',ms.elev,'m')+"</div></div>";

    if (T.weekBars && T.weekBars.length){
      let maxKm = 50;
      T.weekBars.forEach(function(w){ maxKm = Math.max(maxKm, w.actual||0, w.plan||0); });
      let bars = '';
      T.weekBars.forEach(function(w){
        const planW = w.plan ? (w.plan/maxKm*100) : 0;
        const actW = (w.actual/maxKm*100);
        const right = (w.plan!=null) ? ("<strong>"+w.actual+"</strong><span style='color:#A8A29E'> / "+w.plan+"km</span>") : ("<strong>"+w.actual+"</strong><span style='color:#A8A29E'> km</span>");
        bars += "<div style='display:flex;align-items:center;gap:10px'><div style='width:44px;font-size:11px;font-weight:700;color:#78716C;flex-shrink:0'>"+esc(w.label)+"</div><div style='flex:1;position:relative;height:18px;background:#EDECE8;border-radius:5px;overflow:hidden'><div style='position:absolute;left:0;top:0;height:18px;width:"+planW+"%;background:#E0DED9;border-radius:5px'></div><div style='position:absolute;left:0;top:0;height:18px;width:"+actW+"%;background:#FC4C02;border-radius:5px'></div></div><div style='width:86px;font-size:11.5px;text-align:right;flex-shrink:0'>"+right+"</div></div>";
      });
      h += "<div style='background:#fff;border:1px solid #E7E5E1;border-radius:14px;padding:18px'><div style='font-size:12px;font-weight:800;color:#78716C;letter-spacing:.08em;margin-bottom:14px'>週別距離（実績 / 目標）</div><div style='display:flex;flex-direction:column;gap:10px'>"+bars+"</div><div style='display:flex;gap:14px;margin-top:12px;font-size:11px;color:#78716C'><span style='display:inline-flex;align-items:center;gap:5px'><span style='width:10px;height:10px;background:#FC4C02;border-radius:3px;display:inline-block'></span>実績</span><span style='display:inline-flex;align-items:center;gap:5px'><span style='width:10px;height:10px;background:#E0DED9;border-radius:3px;display:inline-block'></span>目標</span></div></div>";
    }

    if (T.showGarmin && T.garmin && T.garmin.length){
      const GL = T.garminLabels || {};
      let rows = '';
      T.garmin.forEach(function(g){
        const rd = parseInt(g.ready);
        const c = (!isNaN(rd)&&rd>=40) ? '#22c55e' : (!isNaN(rd)&&rd>=20) ? '#f59e0b' : '#ef4444';
        const up = g.status==='UNPRODUCTIVE';
        rows += "<div style='display:flex;align-items:center;gap:10px;padding:7px 10px;background:#FAFAF8;border-radius:9px'><div style='width:42px;font-size:11.5px;font-weight:700;color:#57534E;flex-shrink:0'>"+esc(g.date)+"</div><div style='width:70px;flex-shrink:0;display:flex;align-items:center;gap:6px'><div style='width:9px;height:9px;border-radius:50%;background:"+c+";flex-shrink:0'></div><span style='font-size:12px;font-weight:800'>"+esc(g.ready)+"</span></div><div style='font-size:11.5px;color:#78716C;flex:1'>睡眠 <strong style='color:#44403C'>"+esc(g.sleep)+"</strong> ・ "+esc(GL.hv)+" "+esc(g.hv)+" ・ 安静時 "+esc(g.rhr)+"</div><div style='font-size:9.5px;font-weight:800;letter-spacing:.04em;border-radius:5px;padding:2px 7px;flex-shrink:0;background:"+(up?'#FEF2F2':'#FEF6E7')+";color:"+(up?'#B91C1C':'#B45309')+"'>"+esc(g.status)+"</div></div>";
      });
      const vo2 = T.vo2max ? "<div style='font-size:11px;color:#A8A29E'>VO₂Max "+esc(T.vo2max)+"</div>" : '';
      h += "<div style='background:#fff;border:1px solid #E7E5E1;border-radius:14px;padding:18px'><div style='display:flex;justify-content:space-between;align-items:baseline;margin-bottom:14px'><div style='font-size:12px;font-weight:800;color:#78716C;letter-spacing:.08em'>コンディション（Garmin・直近6日）</div>"+vo2+"</div><div style='display:flex;flex-direction:column;gap:6px'>"+rows+"</div></div>";
    }

    if (T.activities && T.activities.length){
      let acts = '';
      T.activities.forEach(function(a){
        const sc = (a.score!=null) ? "<div style='font-size:16px;font-weight:900;color:#D97706'>"+a.score+"</div>" : "<div style='font-size:16px;font-weight:900;color:#57534E'>—</div>";
        acts += "<div style='border:1px solid #EDECE8;border-radius:11px;overflow:hidden'><div class='rr-actrow' data-act='"+a.id+"' style='display:flex;align-items:center;gap:12px;padding:12px 14px;flex-wrap:wrap;cursor:pointer'><div style='width:52px;flex-shrink:0'><div style='font-size:13px;font-weight:800'>"+esc(a.date)+"</div><div style='font-size:10.5px;color:#A8A29E'>"+esc(a.dow)+"</div></div><span style='"+chipStyle(a.typeLabel)+"'>"+esc(a.typeLabel)+"</span><div style='flex:1;min-width:120px'><div style='font-size:13.5px;font-weight:700'>"+esc(a.name)+"</div><div style='font-size:11.5px;color:#78716C'>"+esc(a.dist)+" ・ "+esc(a.pace)+"/km ・ HR "+esc(a.hr)+"</div></div><div style='text-align:right'>"+sc+"<div style='font-size:10px;color:#A8A29E'>スコア</div></div></div><div class='rr-detail' data-detail='"+a.id+"' style='display:none;padding:0 14px 14px'>"+a.detailHtml+"</div></div>";
      });
      h += "<div style='background:#fff;border:1px solid #E7E5E1;border-radius:14px;padding:18px'><div style='font-size:12px;font-weight:800;color:#78716C;letter-spacing:.08em;margin-bottom:12px'>今月のアクティビティ</div><div style='display:flex;flex-direction:column;gap:8px'>"+acts+"</div></div>";
    } else {
      h += "<div style='background:#fff;border:1px solid #E7E5E1;border-radius:14px;padding:18px'><div style='font-size:12px;font-weight:800;color:#78716C;letter-spacing:.08em;margin-bottom:8px'>今月のアクティビティ</div><div style='font-size:12.5px;color:#A8A29E'>この月の記録はまだありません。</div></div>";
    }
    el.innerHTML = h;
    el.querySelectorAll('[data-goto]').forEach(function(a){ a.addEventListener('click', function(e){ e.preventDefault(); setTab(a.getAttribute('data-goto')); }); });
    el.querySelectorAll('.rr-actrow').forEach(function(row){ row.addEventListener('click', function(){
      const id = row.getAttribute('data-act');
      const det = el.querySelector("[data-detail='"+id+"']");
      if (!det) return;
      const show = det.style.display === 'none';
      det.style.display = show ? 'block' : 'none';
      if (show){ const a = T.activities.find(function(x){ return x.id===id; }); if (a && a.mapCoords) initMap(a); }
    }); });
  }

  // ---------- プラン ----------
  function renderPlan(){
    const el = document.getElementById('tab-plan');
    let h = '';
    if (P.days && P.days.length){
      let doneKm = 0;
      P.days.forEach(function(d){ if (state.done[d.id]) doneKm += (d.distKm||0); });
      const tgt = P.targetKm || 0;
      const pct = tgt ? Math.min(100, Math.round(doneKm/tgt*100)) : 0;
      let rows = '';
      P.days.forEach(function(d){
        const done = !!state.done[d.id];
        rows += "<div style='display:flex;align-items:center;column-gap:10px;row-gap:4px;padding:10px 12px;border-radius:10px;flex-wrap:wrap;background:"+(done?'#FAFAF8':'#fff')+";border:1px solid #EDECE8;opacity:"+(done?'.55':'1')+"'><button data-done='"+d.id+"' style='all:unset;box-sizing:border-box;cursor:pointer;width:22px;height:22px;border-radius:7px;flex-shrink:0;text-align:center;line-height:22px;font-size:13px;font-weight:900;border:1px solid "+(done?'#FC4C02':'#D6D3CE')+";background:"+(done?'#FC4C02':'#fff')+";color:#fff'>"+(done?'✓':'')+"</button><div style='width:56px;flex-shrink:0'><span style='font-size:13px;font-weight:800'>"+esc(d.date)+"</span><span style='font-size:11px;color:#A8A29E;margin-left:4px'>"+esc(d.dow)+"</span></div><span style='"+zoneStyle(d.zone)+"'>"+esc(d.zone)+"</span><div style='width:52px;font-size:13px;font-weight:800;flex-shrink:0;text-align:right'>"+esc(d.dist)+"</div><div style='flex:1;min-width:160px;font-size:12px;color:#78716C;line-height:1.6'>"+esc(d.desc)+"</div></div>";
      });
      h += "<div style='background:#fff;border:1px solid #E7E5E1;border-radius:14px;padding:18px'><div style='display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap;margin-bottom:4px'><div style='font-size:15px;font-weight:900'>"+esc(P.title)+"</div><div style='font-size:12px;color:#78716C'>"+esc(P.subtitle||'')+"</div></div><div style='display:flex;align-items:center;gap:10px;margin:10px 0 14px'><div style='flex:1;height:8px;background:#EDECE8;border-radius:4px;overflow:hidden'><div style='height:8px;width:"+pct+"%;background:#FC4C02;border-radius:4px'></div></div><div style='font-size:12px;font-weight:700;color:#78716C'>"+doneKm.toFixed(0)+" / "+tgt.toFixed(0)+"km</div></div><div style='display:flex;flex-direction:column;gap:6px'>"+rows+"</div><div style='margin-top:14px;background:#FFF9F5;border:1px solid #FFE1CF;border-radius:11px;padding:12px 14px;font-size:12px;color:#9A3412;line-height:1.8'>"+esc(P.note)+"</div></div>";
    } else {
      h += "<div style='background:#fff;border:1px solid #E7E5E1;border-radius:14px;padding:18px'><div style='font-size:12px;font-weight:800;color:#78716C;letter-spacing:.08em'>プラン</div><div style='font-size:12.5px;color:#78716C;margin-top:8px;line-height:1.7'>"+esc(P.note)+"</div></div>";
    }
    if (P.months && P.months.length){
      let wk = '';
      P.months.forEach(function(w){
        const open = state.openWeek === w.num;
        let days = '';
        w.days.forEach(function(d){
          days += "<div style='display:flex;align-items:flex-start;gap:10px;padding:8px 10px;background:#fff;border:1px solid #EDECE8;border-radius:9px;flex-wrap:wrap'><div style='width:56px;flex-shrink:0;padding-top:2px'><span style='font-size:12.5px;font-weight:800'>"+esc(d.date)+"</span><span style='font-size:10.5px;color:#A8A29E;margin-left:4px'>"+esc(d.dow)+"</span></div><span style='"+zoneStyle(d.zone)+"'>"+esc(d.zone)+"</span><div style='width:48px;font-size:12.5px;font-weight:800;flex-shrink:0;text-align:right;padding-top:2px'>"+esc(d.dist)+"</div><div style='flex:1;min-width:150px;font-size:11.5px;color:#78716C;line-height:1.6'>"+esc(d.desc)+"</div></div>";
        });
        wk += "<div style='background:#FAFAF8;border-radius:11px;overflow:hidden;border:"+(w.current?'1.5px solid #FC4C02':'1px solid #EDECE8')+"'><button data-week='"+w.num+"' style='all:unset;box-sizing:border-box;display:flex;align-items:center;gap:10px;width:100%;padding:12px 14px;cursor:pointer'><div style='font-size:11px;font-weight:900;border-radius:7px;padding:4px 8px;flex-shrink:0;background:"+(w.current?'#FC4C02':'#E7E5E1')+";color:"+(w.current?'#fff':'#78716C')+";white-space:nowrap'>"+esc(w.numLabel)+"</div><div style='flex:1;min-width:0'><div style='font-size:13px;font-weight:800'>"+esc(w.theme)+"</div><div style='font-size:11px;color:#78716C'>"+esc(w.range)+"</div></div><div style='font-size:13px;font-weight:800;color:#57534E;white-space:nowrap'>"+esc(w.target)+"</div><div style='font-size:11px;color:#A8A29E'>"+(open?'▲':'▼')+"</div></button>"+(open?("<div style='padding:0 14px 12px;display:flex;flex-direction:column;gap:5px'>"+days+"</div>"):'')+"</div>";
      });
      h += "<div style='background:#fff;border:1px solid #E7E5E1;border-radius:14px;padding:18px'><div style='font-size:12px;font-weight:800;color:#78716C;letter-spacing:.08em;margin-bottom:4px'>"+esc(M.monthLabel)+"の月間プラン</div><div style='display:flex;flex-direction:column;gap:8px;margin-top:10px'>"+wk+"</div></div>";
    }
    el.innerHTML = h;
    el.querySelectorAll('[data-done]').forEach(function(b){ b.addEventListener('click', function(){
      const id = b.getAttribute('data-done');
      state.done[id] = !state.done[id];
      try { localStorage.setItem(DONE_KEY, JSON.stringify(state.done)); } catch(e){}
      renderPlan();
    }); });
    el.querySelectorAll('[data-week]').forEach(function(b){ b.addEventListener('click', function(){
      const n = parseInt(b.getAttribute('data-week'));
      state.openWeek = (state.openWeek===n) ? 0 : n;
      renderPlan();
    }); });
  }

  // ---------- AIコーチ ----------
  function renderCoach(){
    const el = document.getElementById('tab-coach');
    const v = C.verdict;
    let pts = '';
    (v.points||[]).forEach(function(p){ pts += "<div style='display:flex;gap:8px'><span style='color:"+p.color+";font-weight:800'>"+esc(p.sym)+"</span><span>"+esc(p.text)+"</span></div>"; });
    let h = "<div style='background:#1C1917;color:#fff;border-radius:14px;padding:20px'><div style='font-size:11px;font-weight:800;color:#A8A29E;letter-spacing:.1em;margin-bottom:10px'>"+esc(v.kicker)+"</div><div style='font-size:17px;font-weight:800;line-height:1.6;margin-bottom:"+(pts?'14px':'0')+"'>"+esc(v.headline)+"</div>"+(pts?("<div style='display:flex;flex-direction:column;gap:8px;font-size:12.5px;color:#D6D3CE;line-height:1.7'>"+pts+"</div>"):'')+"</div>";
    (C.sections||[]).forEach(function(s){
      const open = (s.id in state.openCoach) ? state.openCoach[s.id] : !!s.open;
      h += "<div style='background:#fff;border:1px solid #E7E5E1;border-radius:14px;overflow:hidden'><button data-coach='"+s.id+"' style='all:unset;box-sizing:border-box;display:flex;align-items:center;gap:10px;width:100%;padding:15px 18px;cursor:pointer'><div style='font-size:14px;font-weight:800;flex:1'>"+esc(s.title)+"</div><div style='font-size:11px;color:#A8A29E;white-space:nowrap'>"+esc(s.meta||'')+"</div><div style='font-size:11px;color:#A8A29E'>"+(open?'▲':'▼')+"</div></button>"+(open?("<div class='rr-coachbody' style='padding:0 18px 18px;font-size:13px;color:#44403C;line-height:1.85'>"+s.body+"</div>"):'')+"</div>";
    });
    if (C.keypoints && C.keypoints.length){
      let kp = '';
      C.keypoints.forEach(function(t,i){ kp += "<div style='display:flex;gap:12px'><div style='width:22px;height:22px;border-radius:7px;background:#FC4C02;color:#fff;font-size:12px;font-weight:900;display:flex;align-items:center;justify-content:center;flex-shrink:0'>"+(i+1)+"</div><div style='font-size:12.5px;line-height:1.7;color:#44403C'>"+esc(t)+"</div></div>"; });
      h += "<div style='background:#fff;border:1px solid #E7E5E1;border-radius:14px;padding:18px'><div style='font-size:12px;font-weight:800;color:#78716C;letter-spacing:.08em;margin-bottom:12px'>"+esc(M.monthLabel)+"のキーポイント</div><div style='display:flex;flex-direction:column;gap:10px'>"+kp+"</div></div>";
    }
    let pz = '';
    C.paceZones.forEach(function(z){ pz += "<div style='display:flex;align-items:center;gap:12px;padding:8px 12px;background:#FAFAF8;border-radius:9px'><span style='background:"+z.color+";color:#fff;font-size:11px;font-weight:800;border-radius:6px;padding:3px 9px;width:88px;text-align:center;flex-shrink:0'>"+esc(z.zone)+"</span><div style='font-size:13.5px;font-weight:800;width:104px'>"+esc(z.pace)+"</div><div style='font-size:11.5px;color:#78716C'>"+esc(z.desc)+"</div></div>"; });
    h += "<div style='background:#fff;border:1px solid #E7E5E1;border-radius:14px;padding:18px'><div style='font-size:12px;font-weight:800;color:#78716C;letter-spacing:.08em'>練習ペース目安（VDOT "+C.vdot+" 基準）</div><div style='display:flex;flex-direction:column;gap:6px;margin-top:12px'>"+pz+"</div></div>";
    el.innerHTML = h;
    el.querySelectorAll('[data-coach]').forEach(function(b){ b.addEventListener('click', function(){
      const id = b.getAttribute('data-coach');
      const cur = (id in state.openCoach) ? state.openCoach[id] : false;
      state.openCoach[id] = !cur;
      renderCoach();
    }); });
  }

  // ---------- 記録 ----------
  function renderRecords(){
    const el = document.getElementById('tab-records');
    let cards = '';
    R.pbs.forEach(function(p){
      const ok = p.ok;
      cards += "<div style='background:"+(ok?'#F7FBF7':'#FAFAF8')+";border-radius:11px;padding:12px 14px;border:1px solid "+(ok?'#D3EBD8':'#EDECE8')+"'><div style='display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px'><div style='font-size:11px;font-weight:800;color:#78716C;letter-spacing:.06em'>"+esc(p.dist)+"</div><div style='font-size:10px;font-weight:800;color:"+(ok?'#15803D':'#78716C')+"'>"+esc(p.badge)+"</div></div><div style='font-size:20px;font-weight:900;letter-spacing:-.01em;margin-bottom:2px'>"+esc(p.time)+"</div><div style='font-size:10.5px;color:#A8A29E;margin-bottom:8px'>"+esc(p.date)+"</div><div style='height:5px;background:#EDECE8;border-radius:3px;overflow:hidden'><div style='height:5px;width:"+Math.max(p.pct,3)+"%;border-radius:3px;background:"+(ok?'#22c55e':'#A8A29E')+"'></div></div><div style='font-size:10px;color:#A8A29E;margin-top:5px'>基準 "+esc(p.target)+"</div></div>";
    });
    const focus = R.focus ? "<div style='font-size:11px;color:#A8A29E;margin-bottom:14px'>📌 次の重点距離：<strong style='color:#57534E'>"+esc(R.focus)+"</strong></div>" : '';
    let h = "<div style='background:#fff;border:1px solid #E7E5E1;border-radius:14px;padding:18px'><div style='display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px'><div style='font-size:12px;font-weight:800;color:#78716C;letter-spacing:.08em'>PB 階段（距離別自己ベスト）</div><div style='font-size:11px;color:#A8A29E'>Sub 3:10 相当と比較</div></div>"+focus+"<div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px'>"+cards+"</div></div>";
    const vd = R.vdot;
    h += "<div style='background:#fff;border:1px solid #E7E5E1;border-radius:14px;padding:18px'><div style='font-size:12px;font-weight:800;color:#78716C;letter-spacing:.08em;margin-bottom:18px'>VDOT 進捗 — Sub 3:00 への道</div><div style='position:relative;height:10px;background:linear-gradient(90deg,#E0DED9 0%,#93B4F5 60%,#F5C063 73%,#F08A8A 100%);border-radius:5px;margin:0 8px'><div style='position:absolute;left:0%;top:16px;font-size:10.5px;color:#78716C;transform:translateX(-4px)'>VDOT "+vd.pbVdot+"<br><span style='font-weight:700'>PB "+esc(vd.pbStr)+"</span></div><div style='position:absolute;left:"+vd.markerPct+"%;top:-30px;transform:translateX(-50%);text-align:center'><div style='font-size:10.5px;font-weight:900;color:#2563EB;white-space:nowrap'>現在 "+vd.current+"</div></div><div style='position:absolute;left:"+vd.markerPct+"%;top:-6px;transform:translateX(-50%);width:22px;height:22px;background:#fff;border:4px solid #2563EB;border-radius:50%'></div><div style='position:absolute;left:73%;top:16px;transform:translateX(-50%);font-size:10.5px;color:#B45309;text-align:center;white-space:nowrap'>VDOT 59<br><span style='font-weight:800'>Sub 3:10</span></div><div style='position:absolute;right:0;top:16px;font-size:10.5px;color:#B91C1C;text-align:right;white-space:nowrap'>VDOT 63<br><span style='font-weight:800'>Sub 3:00</span></div></div><div style='height:48px'></div><div style='background:#FAFAF8;border-radius:11px;padding:14px 16px;font-size:12.5px;color:#57534E;line-height:1.9'><strong style='color:#1C1917'>Sub 3:00 への 3 つの柱</strong><br>① 週間走行量 <strong>70〜80km</strong>（現在 ~50km）<br>② 月2回以上の <strong>30km ロング走</strong><br>③ 週1回の <strong>テンポ走 or インターバル</strong></div></div>";
    let tg = '';
    R.targets.forEach(function(t){ tg += "<div style='background:#fff;border:1px solid #E7E5E1;border-radius:14px;padding:18px;text-align:center'><div style='font-size:11px;font-weight:800;color:#78716C;letter-spacing:.1em;margin-bottom:6px'>"+esc(t.kicker)+"</div><div style='font-size:30px;font-weight:900;color:"+t.color+"'>"+esc(t.value)+"</div><div style='font-size:11.5px;color:#A8A29E;margin-top:4px'>"+esc(t.sub)+"</div></div>"; });
    h += "<div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px'>"+tg+"</div>";
    el.innerHTML = h;
  }

  function initMap(a){
    if (!a.mapCoords || mapInited[a.id]) return;
    if (typeof L === 'undefined') return;
    try {
      const m = L.map(a.mapId, { zoomControl:true, attributionControl:false });
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(m);
      const poly = L.polyline(a.mapCoords, { color:a.mapColor||'#FC4C02', weight:3, opacity:0.85 }).addTo(m);
      m.fitBounds(poly.getBounds(), { padding:[12,12] });
      L.circleMarker(a.mapCoords[0], { radius:6,color:'#fff',fillColor:'#22c55e',fillOpacity:1,weight:2 }).addTo(m);
      L.circleMarker(a.mapCoords[a.mapCoords.length-1], { radius:6,color:'#fff',fillColor:'#ef4444',fillOpacity:1,weight:2 }).addTo(m);
      mapInited[a.id] = true;
      setTimeout(function(){ m.invalidateSize(); }, 60);
    } catch(e){}
  }

  function setTab(tab){
    if (TABS.indexOf(tab) < 0) tab = 'today';
    state.tab = tab;
    try { localStorage.setItem('rr2-tab', tab); } catch(e){}
    if (location.hash !== '#'+tab){ try { history.replaceState(null,'','#'+tab); } catch(e){ location.hash = tab; } }
    document.querySelectorAll('.rr-tab').forEach(function(b){
      const active = b.getAttribute('data-tab') === tab;
      b.style.color = active ? '#1C1917' : '#78716C';
      b.style.borderBottom = active ? '3px solid #FC4C02' : '3px solid transparent';
    });
    TABS.forEach(function(k){ document.getElementById('tab-'+k).style.display = (k===tab) ? 'flex' : 'none'; });
  }

  renderToday(); renderPlan(); renderCoach(); renderRecords();
  document.querySelectorAll('.rr-tab').forEach(function(b){ b.addEventListener('click', function(){ setTab(b.getAttribute('data-tab')); }); });
  window.addEventListener('hashchange', function(){ const t = (location.hash||'').replace('#',''); if (TABS.indexOf(t)>=0 && t!==state.tab) setTab(t); });
  let start = (location.hash||'').replace('#','');
  if (TABS.indexOf(start) < 0){ try { start = localStorage.getItem('rr2-tab') || 'today'; } catch(e){ start = 'today'; } }
  setTab(start);

  // スクロールでヘッダーを折りたたむ（モバイルで面積を圧縮。ヒステリシスでちらつき防止）
  (function(){
    const header = document.querySelector('.rr-header');
    if (!header) return;
    let collapsed = false, ticking = false;
    function apply(){
      ticking = false;
      const y = window.scrollY || document.documentElement.scrollTop || 0;
      const want = collapsed ? (y > 60) : (y > 110);
      if (want !== collapsed){ collapsed = want; header.classList.toggle('rr-collapsed', collapsed); }
    }
    window.addEventListener('scroll', function(){
      if (!ticking){ ticking = true; window.requestAnimationFrame(apply); }
    }, { passive: true });
    apply();
  })();
})();
"""

BANNER_JS = r"""(function () {
  const host = location.hostname;
  const isLocal = host === 'localhost' || host === '127.0.0.1'
    || /^100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\./.test(host)  // Tailscale CGNAT 100.64.0.0/10
    || host.endsWith('.ts.net');
  const isOnline = host.endsWith('.github.io');
  const edition = isLocal ? 'local' : (isOnline ? 'online' : 'other');
  const banner = document.getElementById('edition-banner');
  const onlineUrl = "__ONLINE_URL__";
  const localUrl = "__LOCAL_URL__";
  const baseTitle = document.title.replace(/ \[(ローカル|オンライン)\]/g, '');
  if (!banner) return;
  if (edition === 'local') {
    banner.className = 'edition-banner edition-local';
    banner.innerHTML = '💻 <strong>ローカル版</strong>（この Mac · serve_report）'
      + ' — 右上のボタンで更新 · 自動更新なし'
      + ' · <a href="' + onlineUrl + '" target="_blank" rel="noopener">オンライン版を開く</a>';
    document.title = baseTitle + ' [ローカル]';
  } else if (edition === 'online') {
    banner.className = 'edition-banner edition-online';
    banner.innerHTML = '🌐 <strong>オンライン版</strong>（GitHub Pages）'
      + ' — 毎日 23:00 JST 自動更新 · Strava同期ボタン'
      + ' · <a href="' + localUrl + '">ローカル版</a>';
    document.title = baseTitle + ' [オンライン]';
  } else {
    banner.className = 'edition-banner edition-other';
    banner.innerHTML = '⚠️ file:// では更新できません — ターミナルで <code>python3 serve_report.py --open</code> を実行し '
      + '<a href="' + localUrl + '">' + localUrl + '</a> をブックマークしてください';
    document.title = baseTitle + ' [ローカル推奨]';
  }
})();"""

AUTH_JS = r"""(function () {
  const panel = document.getElementById('update-panel');
  const btnUpdate = document.getElementById('btn-update');
  const btnCoach = document.getElementById('btn-coach');
  const btnGarmin = document.getElementById('btn-garmin');
  const hint = document.getElementById('update-hint');
  const statusBox = document.getElementById('update-status');
  const statusText = document.getElementById('update-status-text');
  const logEl = document.getElementById('update-log');
  const host = location.hostname;
  const isLocal = host === 'localhost' || host === '127.0.0.1'
    || /^100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\./.test(host)  // Tailscale CGNAT 100.64.0.0/10
    || host.endsWith('.ts.net');
  const isGithubPages = host.endsWith('.github.io');
  const githubPanel = document.getElementById('github-sync-panel');
  const token = __TOKEN__;
  const authHeaders = token ? { 'X-Report-Token': token } : {};
  let pollTimer = null;
  let activeKind = null;

  if (isGithubPages && githubPanel && panel) {
    panel.style.display = 'none';
    githubPanel.style.display = 'flex';
  }

  if (!panel || !btnUpdate || !btnCoach) return;
  if (!isLocal) { return; }

  if (location.protocol === 'file:') {
    hint.textContent = 'file:// では不可 → python3 serve_report.py --open';
  }

  function setButtonsDisabled(disabled) {
    btnUpdate.disabled = disabled;
    btnCoach.disabled = disabled;
    if (btnGarmin) btnGarmin.disabled = disabled;
  }
  function setStatus(msg, isError, isSuccess) {
    statusBox.classList.add('visible');
    statusBox.classList.toggle('error', !!isError);
    statusBox.classList.toggle('success', !!isSuccess);
    statusText.textContent = msg;
  }
  function stopPoll() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }
  function stepLabel(data) {
    const labels = { starting:'準備中…', fetch:'Strava から取得中…', coach:'Claude が評価中…',
      garmin:'Garmin から取得中…（数分かかります）', html:'HTML 再生成中…', done:'完了', error:'エラー' };
    return labels[data.step] || data.step;
  }
  async function pollStatus() {
    try {
      const res = await fetch('/api/status', { headers: authHeaders });
      const data = await res.json();
      const kind = data.kind || activeKind;
      const isSuccess = data.done && !data.running && !data.error;
      if (!isSuccess) { setStatus(stepLabel(data), !!data.error, false); }
      logEl.textContent = (data.log || []).slice(-12).join('\n');
      if (data.error) { setButtonsDisabled(false); stopPoll(); return; }
      if (data.done && !data.running) {
        stopPoll();
        if (kind === 'coach') {
          const ts = data.last_coach || '';
          setStatus(ts ? ('✓ AI コーチング完了 — ' + ts) : '✓ AI コーチング完了', false, true);
          const lastCoachEl = document.getElementById('last-coach-msg');
          if (lastCoachEl && ts) { lastCoachEl.className = 'last-coach ok'; lastCoachEl.textContent = '✓ 最終 AI 評価: ' + ts; }
        } else if (kind === 'garmin') {
          setStatus('✓ Garmin 取得・レポート再生成 完了', false, true);
          const lg = document.getElementById('last-garmin-msg');
          if (lg) lg.className = 'last-garmin ok';
        } else {
          const ts = data.last_fetch || '';
          setStatus(ts ? ('✓ 更新完了 — データ ' + ts) : '✓ 更新完了', false, true);
          const lastFetchEl = document.getElementById('last-fetch-msg');
          if (lastFetchEl && ts) { lastFetchEl.className = 'last-fetch ok'; lastFetchEl.textContent = '✓ 最終データ取得: ' + ts; }
          const coachTs = data.last_coach || '';
          const lastCoachEl = document.getElementById('last-coach-msg');
          if (lastCoachEl && coachTs) { lastCoachEl.className = 'last-coach ok'; lastCoachEl.textContent = '✓ 最終 AI 評価: ' + coachTs; }
          const warnEl = document.getElementById('last-coach-warn');
          if (warnEl) warnEl.remove();
        }
        setTimeout(function(){ location.reload(); }, 2500);
      }
    } catch (e) {
      setStatus('サーバーに接続できません', true, false);
      setButtonsDisabled(false);
      stopPoll();
    }
  }
  function warnFileProtocol() {
    alert('file:// では実行できません。\n\nターミナルで:\n  cd ~/Projects/strava-report\n  python3 serve_report.py --open\n\nを実行し、http://127.0.0.1:8766/index.html を開いてください。');
  }
  async function startJob(endpoint, kind, startMsg) {
    if (location.protocol === 'file:') { warnFileProtocol(); return; }
    activeKind = kind;
    setButtonsDisabled(true);
    setStatus(startMsg, false, false);
    logEl.textContent = '';
    try {
      const res = await fetch(endpoint, { method: 'POST', headers: authHeaders });
      const data = await res.json();
      if (!data.started) {
        setStatus(data.message || 'すでに処理が実行中です', false, false);
        if (data.message) logEl.textContent = data.message;
        setButtonsDisabled(false);
        return;
      }
      stopPoll();
      pollTimer = setInterval(pollStatus, 1500);
      pollStatus();
    } catch (e) {
      setStatus('API に接続できません。serve_report.py が起動しているか確認してください。', true, false);
      setButtonsDisabled(false);
    }
  }
  btnUpdate.addEventListener('click', function(){ startJob('/api/update', 'fetch', 'データ更新を開始しています…'); });
  btnCoach.addEventListener('click', function(){ startJob('/api/coach', 'coach', 'AI 評価を開始しています…（Claude API）'); });
  if (btnGarmin) { btnGarmin.addEventListener('click', function(){ startJob('/api/garmin', 'garmin', 'Garmin から取得を開始しています…（数分かかります）'); }); }
})();"""

_data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
render_js = RENDER_JS.replace("/*__DATA__*/", _data_json)
banner_js = (BANNER_JS
             .replace("__ONLINE_URL__", GITHUB_PAGES_URL)
             .replace("__LOCAL_URL__", LOCAL_REPORT_URL))
auth_js = AUTH_JS.replace("__TOKEN__", json.dumps(REPORT_SERVER_TOKEN))

html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>🏃 Strava {MONTH_LABEL} ランニングレポート</title>
<meta name="report-edition" content="{REPORT_EDITION}">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>{CSS}</style>
</head>
<body>

<div id="edition-banner" class="edition-banner" role="status" aria-live="polite"></div>
<script>{banner_js}</script>

<div class="rr-header">
  <div class="rr-header-inner">
    <div class="rr-header-row1">
      <div class="rr-title">
        <div class="rr-bar"></div>
        <div class="rr-month">{MONTH_LABEL}</div>
        <div class="rr-sub">ランニングレポート</div>
      </div>
      <div class="header-actions" id="update-panel">
        <button type="button" class="btn-update" id="btn-update">🔄 更新</button>
        <button type="button" class="btn-coach" id="btn-coach">🤖 AI</button>
        {garmin_btn_html}
        <span class="update-hint" id="update-hint"></span>
      </div>
      <div class="header-actions" id="github-sync-panel">
        <a class="btn-sync-link" id="btn-github-sync" href="{GITHUB_WORKFLOW_URL}" target="_blank" rel="noopener">🔄 Strava同期</a>
      </div>
      {build_month_nav()}
    </div>
    <div class="rr-meta">
      {last_fetch_banner}
      {last_garmin_banner}
      {last_coach_banner}
    </div>
    <div class="update-status" id="update-status">
      <strong id="update-status-text"></strong>
      <div class="update-log" id="update-log"></div>
    </div>
    <div class="rr-tabs">
      <button class="rr-tab" data-tab="today">今日</button>
      <button class="rr-tab" data-tab="plan">プラン</button>
      <button class="rr-tab" data-tab="coach">AIコーチ</button>
      <button class="rr-tab" data-tab="records">記録</button>
    </div>
  </div>
</div>

<div class="rr-main">
  <section id="tab-today" style="display:none"></section>
  <section id="tab-plan" style="display:none"></section>
  <section id="tab-coach" style="display:none"></section>
  <section id="tab-records" style="display:none"></section>
</div>

<script>{auth_js}</script>
<script>
{render_js}
</script>
</body>
</html>
"""

with open(ARCHIVE_FILE, "w", encoding="utf-8") as f:
    f.write(html)
with open(OUTPUT, "w", encoding="utf-8") as f:
    f.write(html)

print(f"✓ {ARCHIVE_FILE} を生成しました（月別アーカイブ）")
print(f"✓ {OUTPUT} を更新しました（当月）")

if REPORT_EDITION == "online":
    publish_meta = {
        "published_by": "actions" if os.environ.get("GITHUB_ACTIONS") == "true" else "local",
        "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "garmin": bool(_g_updated),
        "garmin_updated_at": _g_updated.isoformat() if _g_updated else None,
    }
    with open("publish_meta.json", "w", encoding="utf-8") as f:
        json.dump(publish_meta, f, ensure_ascii=False, indent=2)
    print(f"✓ publish_meta.json 更新（published_by={publish_meta['published_by']}, garmin={publish_meta['garmin']}）")
print(f"  → open \"{OUTPUT}\"")

#!/usr/bin/env python3
"""
Strava HTML レポート生成（現在月を自動検出）
環境変数 TARGET_YEAR_MONTH=YYYY-MM で月を指定可能（省略時は当月）
"""

import csv, json, os, glob, re
from collections import defaultdict
from datetime import date, timedelta
import calendar as _cal_mod

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
STREAMS_CSV   = "gps_streams.csv"
ARCHIVE_FILE  = f"{YYYYMM}.html"   # 月別アーカイブ（永続）
OUTPUT        = "index.html"        # 常に当月を index.html にも書く

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
    # 現在生成中の月が未ファイルでも必ずタブに含める
    current = (TARGET_YEAR, TARGET_MONTH, ARCHIVE_FILE)
    if current not in months:
        months.append(current)
        months.sort(reverse=True)
    if len(months) <= 1:
        return ""
    tabs = ""
    for y, mo, fname in months:
        is_cur = (y == TARGET_YEAR and mo == TARGET_MONTH)
        label  = f"{y}年{mo}月{'（当月）' if is_cur else ''}"
        style  = "month-tab-active" if is_cur else "month-tab"
        tabs  += f'<a href="{fname}" class="{style}">{label}</a>'
    return f'<nav class="month-nav">{tabs}</nav>'

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
_VO2MAX       = 59    # Garmin 計測 VO2Max（HTML上でスライダー変更可能）
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
    today      = date.today()
    mon        = today - timedelta(days=today.weekday())
    week_dates = [mon + timedelta(days=i) for i in range(7)]
    day_labels = ["月", "火", "水", "木", "金", "土", "日"]

    # 今週の実績をdateごとに集める（複数走行対応）
    runs_by_date = defaultdict(list)
    for r in runs:
        try:
            d = date.fromisoformat(r["date"])
            if mon <= d <= mon + timedelta(days=6):
                runs_by_date[d].append(r)
        except: pass

    rows           = ""
    week_actual_km = 0.0
    week_target_km = sum(t[3] for t in _WEEKLY_PLAN)
    _type_names    = {"easy": "イージー", "interval": "インターバル",
                      "tempo": "テンポ走",  "long": "ロング走", "rest": "休養"}

    for i, (_, plan_type, plan_label, plan_dist, plan_desc) in enumerate(_WEEKLY_PLAN):
        d         = week_dates[i]
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

    return f"""
    <div class="plan-box" style="margin-bottom:24px">
      <div class="plan-label">📅 今週の練習メニュー（{week_start_str}〜{week_end_str} / 週{_WEEKLY_PLAN_RANGE}km目標）</div>
      <div style="font-size:11px;color:#a0aec0;margin-bottom:10px">
        固定プランに対する実績を自動評価。月曜始まり・VDOT 51（現走力）基準。
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

# ── HTML 生成 ──────────────────────────────────────────────────────────────
hh, rem = divmod(total_sec, 3600); mm = rem // 60

html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>🏃 Strava {MONTH_LABEL} ランニングレポート</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0 }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          background: #f0f4f8; color: #1a202c; line-height: 1.6 }}
  .header {{ background: linear-gradient(135deg, #fc4c02 0%, #e63800 100%);
             color: white; padding: 32px 40px }}
  .header h1 {{ font-size: 28px; font-weight: 700 }}
  .header p  {{ opacity: 0.85; margin-top: 4px }}
  .container {{ max-width: 1100px; margin: 0 auto; padding: 32px 20px }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
             gap: 16px; margin-bottom: 32px }}
  .card {{ background: #fff; border-radius: 12px; padding: 20px 24px;
           box-shadow: 0 1px 4px rgba(0,0,0,.08) }}
  .card .label {{ font-size: 12px; color: #718096; text-transform: uppercase;
                  letter-spacing: .06em; margin-bottom: 6px }}
  .card .value {{ font-size: 28px; font-weight: 700; color: #2d3748 }}
  .card .sub   {{ font-size: 12px; color: #a0aec0; margin-top: 2px }}
  .section {{ background: #fff; border-radius: 12px; padding: 24px;
              box-shadow: 0 1px 4px rgba(0,0,0,.08); margin-bottom: 24px }}
  .section h2 {{ font-size: 16px; font-weight: 600; color: #2d3748;
                 border-left: 3px solid #fc4c02; padding-left: 10px; margin-bottom: 20px }}
  .charts {{ display: grid; grid-template-columns: 2fr 1fr; gap: 24px; margin-bottom: 24px }}
  canvas {{ width: 100% !important }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px }}
  thead tr {{ background: #f7fafc }}
  th {{ text-align: left; padding: 10px 12px; font-size: 11px; text-transform: uppercase;
        letter-spacing: .05em; color: #718096; border-bottom: 2px solid #e2e8f0 }}
  td {{ padding: 10px 12px; border-bottom: 1px solid #edf2f7 }}
  tr:last-child td {{ border-bottom: none }}
  tr:hover td {{ background: #f7fafc }}
  .lap-card {{ border: 1px solid #e2e8f0; border-radius: 8px; margin-bottom: 16px;
               overflow: hidden }}
  .lap-title {{ background: #f7fafc; padding: 10px 16px; font-size: 13px;
                font-weight: 600; color: #2d3748 }}
  .lap-table {{ font-size: 12px }}
  .lap-table th, .lap-table td {{ padding: 7px 16px }}
  .coach-card {{ border: 1px solid #e2e8f0; border-radius: 10px;
                 margin-bottom: 16px; overflow: hidden; background: #fff }}
  .coach-header {{ display: flex; align-items: center; gap: 12px;
                   padding: 12px 16px; background: #f8fafc; flex-wrap: wrap }}
  .coach-badge {{ font-size: 12px; font-weight: 700; color: #fff;
                  padding: 3px 10px; border-radius: 20px; white-space: nowrap }}
  .coach-date {{ font-size: 13px; font-weight: 600; color: #2d3748 }}
  .coach-date small {{ display: block; font-weight: 400; color: #718096; margin-top: 1px }}
  .coach-body {{ padding: 16px 20px; display: flex; flex-direction: column; gap: 10px }}
  .coach-purpose {{ font-size: 13px; color: #4a5568; line-height: 1.7;
                    background: #f0fdf4; padding: 10px 14px; border-radius: 6px }}
  .coach-assessment {{ font-size: 13px; color: #2d3748; line-height: 1.7 }}
  .coach-tips {{ font-size: 12px; color: #718096; padding-left: 20px; line-height: 1.8;
                 background: #fffbeb; padding: 10px 10px 10px 28px; border-radius: 6px }}
  .coach-layout {{ display: grid; grid-template-columns: 1fr 340px; gap: 0 }}
  .run-map {{ height: 220px; border-left: 1px solid #e2e8f0 }}
  .no-map  {{ display:flex;align-items:center;justify-content:center;
              color:#cbd5e0;font-size:12px;background:#f7fafc }}
  .score-wrap {{ display:flex;align-items:center;gap:6px;flex-wrap:wrap }}
  .score-num  {{ font-size:22px;font-weight:800 }}
  .score-unit {{ font-size:12px;color:#a0aec0;margin-right:4px }}
  .score-label {{ font-size:11px;color:#fff;padding:2px 8px;border-radius:10px;font-weight:600 }}
  .score-bar-bg {{ width:80px;height:6px;background:#e2e8f0;border-radius:3px }}
  .score-bar-fg {{ height:6px;border-radius:3px;transition:width .4s }}
  .score-breakdown {{ display:flex;gap:12px;flex-wrap:wrap;font-size:12px;color:#718096;
                      background:#f8fafc;padding:8px 12px;border-radius:6px }}
  .score-breakdown span {{ font-weight:600;color:#4a5568 }}
  .top-plan {{ margin-bottom:24px }}
  .top-plan-grid {{ display:grid;grid-template-columns:1fr 200px;gap:16px;margin-bottom:16px }}
  .plan-box {{ background:#fff;border-radius:12px;padding:18px 20px;
               box-shadow:0 1px 4px rgba(0,0,0,.08) }}
  .plan-label {{ font-size:12px;font-weight:700;color:#718096;text-transform:uppercase;
                 letter-spacing:.06em;margin-bottom:10px }}
  .trend-box {{ }}
  .race-box {{ text-align:center;background:linear-gradient(135deg,#fff5f5,#fff) }}
  .race-name {{ font-size:15px;font-weight:700;color:#dc2626;margin-bottom:2px }}
  .race-date {{ font-size:13px;color:#718096;margin-bottom:8px }}
  .race-days {{ font-size:13px;color:#4a5568 }}
  .race-days span {{ font-size:32px;font-weight:800;color:#dc2626;display:block }}
  /* PB 階段 */
  .pb-ladder {{ display:grid;grid-template-columns:repeat(6,1fr);gap:10px }}
  .pb-card {{ background:#f8fafc;border-radius:10px;padding:12px 10px;text-align:center;
              border:1px solid #e2e8f0 }}
  .pb-dist {{ font-size:11px;font-weight:700;color:#718096;text-transform:uppercase;
              letter-spacing:.05em;margin-bottom:4px }}
  .pb-time {{ font-size:17px;font-weight:800;color:#2d3748;margin-bottom:2px }}
  .pb-date {{ font-size:10px;color:#a0aec0;margin-bottom:8px }}
  .pb-bar-bg {{ height:6px;background:#e2e8f0;border-radius:3px;margin-bottom:6px }}
  .pb-bar-fg {{ height:6px;border-radius:3px;transition:width .4s }}
  .pb-targets {{ display:flex;justify-content:space-between;align-items:center }}
  @media(max-width:700px){{ .pb-ladder {{ grid-template-columns:repeat(3,1fr) }} }}
  @media(max-width:400px){{ .pb-ladder {{ grid-template-columns:repeat(2,1fr) }} }}
  /* パフォーマンスプロフィール */
  .perf-section {{ margin-bottom:24px;display:flex;flex-direction:column;gap:16px }}
  .perf-grid {{ display:grid;grid-template-columns:1fr 220px;gap:16px }}
  .perf-pace-grid {{ display:grid;grid-template-columns:1fr 1fr;gap:16px }}
  .perf-box {{ background:#fff;border-radius:12px;padding:18px 20px;
               box-shadow:0 1px 4px rgba(0,0,0,.08) }}
  .perf-box table td {{ padding:7px 10px;border-bottom:1px solid #f0f4f8;font-size:13px }}
  .perf-box table tr:last-child td {{ border-bottom:none }}
  .pace-badge {{ color:#fff;padding:2px 7px;border-radius:10px;font-size:11px;font-weight:700;white-space:nowrap }}
  /* テーブルスクロール */
  .table-scroll {{ overflow-x:auto;-webkit-overflow-scrolling:touch }}
  /* ── モバイル対応（iPhone 縦型 ≈ 390px） ── */
  @media(max-width:700px){{
    .top-plan-grid,.perf-grid,.perf-pace-grid {{ grid-template-columns:1fr }}
    .container {{ padding:16px 12px }}
    .header {{ padding:20px 16px }}
    .header h1 {{ font-size:20px }}
    .cards {{ grid-template-columns:repeat(2,1fr);gap:10px }}
    .card {{ padding:14px 16px }}
    .card .value {{ font-size:22px }}
  }}
  @media (max-width: 800px) {{
    .charts {{ grid-template-columns: 1fr }}
    .coach-layout {{ grid-template-columns: 1fr }}
    .run-map {{ border-left:none;border-top:1px solid #e2e8f0 }}
  }}
  /* 月別ナビゲーション */
  .month-nav {{ background:#1a202c;padding:0 20px;display:flex;gap:4px;overflow-x:auto;
               -webkit-overflow-scrolling:touch }}
  .month-tab,.month-tab-active {{ display:inline-block;padding:10px 16px;font-size:13px;
    font-weight:600;text-decoration:none;white-space:nowrap;border-bottom:3px solid transparent }}
  .month-tab {{ color:#a0aec0 }}
  .month-tab:hover {{ color:#fff }}
  .month-tab-active {{ color:#fc4c02;border-bottom-color:#fc4c02 }}
  @media(max-width:480px){{
    .month-tab,.month-tab-active {{ padding:8px 12px;font-size:12px }}
  }}
  @media(max-width:480px){{
    /* アクティビティ表：標高列を非表示 */
    .act-col-elev {{ display:none }}
    /* レース週テーブル：日付列を縮小、ラベル列を非表示 */
    .rw-col-label {{ display:none }}
    .rw-col-date {{ font-size:11px;white-space:normal!important }}
    .rw-col-menu {{ font-size:12px }}
    /* スコア内訳 */
    .score-breakdown {{ font-size:11px;gap:8px }}
    .score-bar-bg {{ display:none }}
    .score-num {{ font-size:18px }}
    /* ラップ表 */
    .lap-table th,.lap-table td {{ padding:5px 8px;font-size:11px }}
    /* コーチヘッダー */
    .coach-header {{ flex-direction:column;align-items:flex-start }}
    .coach-date small {{ display:inline;margin-left:6px }}
    /* section padding 縮小 */
    .section {{ padding:16px 12px }}
    .plan-box {{ padding:14px 14px }}
    .perf-box {{ padding:14px 14px }}
  }}
</style>
</head>
<body>

{build_month_nav()}

<div class="header">
  <h1>🏃 {MONTH_LABEL} ランニングレポート</h1>
  <p>Strava データより生成 — {date.today()}</p>
</div>

<div class="container">

  {build_pb_ladder(load_pbs())}

  {build_performance_profile()}

  {build_top_plan(runs)}

  {build_weekly_menu(runs)}

  <!-- サマリーカード -->
  <div class="cards">
    <div class="card">
      <div class="label">総距離</div>
      <div class="value">{total_dist:.1f}</div>
      <div class="sub">km</div>
    </div>
    <div class="card">
      <div class="label">ランニング回数</div>
      <div class="value">{len(runs)}</div>
      <div class="sub">回</div>
    </div>
    <div class="card">
      <div class="label">総時間</div>
      <div class="value">{hh}:{mm:02d}</div>
      <div class="sub">時間:分</div>
    </div>
    <div class="card">
      <div class="label">平均心拍</div>
      <div class="value">{avg_hr:.0f}</div>
      <div class="sub">bpm</div>
    </div>
    <div class="card">
      <div class="label">累積獲得標高</div>
      <div class="value">{total_elev:.0f}</div>
      <div class="sub">m</div>
    </div>
  </div>

  <!-- グラフ -->
  <div class="charts">
    <div class="section">
      <h2>週別距離</h2>
      <canvas id="weekChart" height="180"></canvas>
    </div>
    <div class="section">
      <h2>種別内訳</h2>
      <canvas id="typeChart" height="180"></canvas>
    </div>
  </div>

  <div class="section">
    <h2>ペース推移</h2>
    <canvas id="paceChart" height="120"></canvas>
  </div>

  <!-- アクティビティ一覧 -->
  <div class="section">
    <h2>アクティビティ一覧</h2>
    <div class="table-scroll">
    <table>
      <thead>
        <tr>
          <th>日付</th><th>名前</th><th>距離</th><th>タイム</th>
          <th>ペース</th><th>HR</th><th class="act-col-elev">標高</th><th>種別</th>
        </tr>
      </thead>
      <tbody>
        {activity_rows()}
      </tbody>
    </table>
    </div>
  </div>

  <!-- コーチングレビュー -->
  <div class="section">
    <h2>🏅 練習レビュー（1.5km以上 / VO₂Max 59 基準 / ダニエルズ・Pfitzinger・Hansons・80/20）</h2>
    {coaching_sections(gps_map)}
  </div>

  <!-- ラップ詳細 -->
  <div class="section">
    <h2>ラップ詳細</h2>
    {lap_sections()}
  </div>

</div>

<script>
const orange = '#fc4c02', blue = '#3b82f6', green = '#22c55e',
      purple = '#6366f1', yellow = '#f59e0b', red = '#ef4444';

// 週別距離
new Chart(document.getElementById('weekChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(week_keys, ensure_ascii=False)},
    datasets: [{{
      label: '距離 (km)',
      data: {json.dumps(week_dists)},
      backgroundColor: orange + 'cc',
      borderColor: orange,
      borderWidth: 1, borderRadius: 6
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      y: {{ beginAtZero: true, title: {{ display: true, text: 'km' }} }}
    }}
  }}
}});

// 種別ドーナツ
new Chart(document.getElementById('typeChart'), {{
  type: 'doughnut',
  data: {{
    labels: {json.dumps(type_labels, ensure_ascii=False)},
    datasets: [{{
      data: {json.dumps(type_values)},
      backgroundColor: {json.dumps(type_colors)},
      borderWidth: 2, borderColor: '#fff'
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ position: 'bottom', labels: {{ font: {{ size: 11 }} }} }} }}
  }}
}});

// ペース推移（分/km）
new Chart(document.getElementById('paceChart'), {{
  type: 'line',
  data: {{
    labels: {json.dumps(pace_labels, ensure_ascii=False)},
    datasets: [
      {{
        label: 'ペース (分/km)',
        data: {json.dumps(pace_data)},
        borderColor: orange, backgroundColor: orange + '22',
        borderWidth: 2, pointRadius: 4, tension: 0.3,
        yAxisID: 'y'
      }},
      {{
        label: '距離 (km)',
        data: {json.dumps(dist_data)},
        borderColor: blue, backgroundColor: blue + '22',
        borderWidth: 2, pointRadius: 3, tension: 0.3,
        yAxisID: 'y2', borderDash: [4,3]
      }}
    ]
  }},
  options: {{
    responsive: true,
    interaction: {{ mode: 'index', intersect: false }},
    plugins: {{ legend: {{ labels: {{ font: {{ size: 11 }} }} }} }},
    scales: {{
      y: {{
        title: {{ display: true, text: '分/km' }},
        reverse: true,
        ticks: {{
          callback: v => {{
            const m = Math.floor(v), s = Math.round((v - m) * 60);
            return m + ':' + String(s).padStart(2,'0');
          }}
        }}
      }},
      y2: {{ position: 'right', title: {{ display: true, text: 'km' }},
              grid: {{ drawOnChartArea: false }} }}
    }}
  }}
}});
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
print(f"  → open \"{OUTPUT}\"")

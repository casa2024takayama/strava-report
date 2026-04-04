#!/usr/bin/env python3
"""
Strava HTML レポート生成（現在月を自動検出）
環境変数 TARGET_YEAR_MONTH=YYYY-MM で月を指定可能（省略時は当月）
"""

import csv, json, os
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

RUNS_CSV    = f"runs_{YYYYMM}.csv"
LAPS_CSV    = f"runs_{YYYYMM}_laps.csv"
STREAMS_CSV = "gps_streams.csv"
OUTPUT      = "index.html"

# ── アスリートプロフィール（表示には使用しない） ───────────────────────────
_WEIGHT_KG    = 65
_HEIGHT_CM    = 174
_MONTH_GOAL   = 200   # km/月

# ── レーススケジュール（過去も含めて列挙、自動で次のレースを選択） ─────────
_RACES = [
    (date(2026, 3,  1),  "東京マラソン2026",    42.195),
    (date(2026, 3, 28),  "ふくい桜マラソン",    42.195),
    # ↓ 次のレースをここに追加
    # (date(2026, 10, 18), "大阪マラソン",       42.195),
]
_today_ref = date.today()
_next_races  = [(d, n, dist) for d, n, dist in _RACES if d >= _today_ref]
_NEXT_RACE   = _next_races[0][0]   if _next_races else None
_RACE_NAME   = _next_races[0][1]   if _next_races else None
_RACE_DIST   = _next_races[0][2]   if _next_races else 42.195
_VO2MAX       = 59    # Garmin 計測 VO2Max（HTML上でスライダー変更可能）
_CURRENT_PB   = "3:21:00"   # フルマラソン自己ベスト（非表示）
_CURRENT_PB_SEC = 3*3600 + 21*60  # 12060 sec
_GOAL_1_SEC   = 3*3600 + 10*60   # Sub 3:10
_GOAL_ULT_SEC = 3*3600            # Sub 3:00
# VDOT 59 ダニエルズ基準ペース（秒/km）※スライダーで動的変更
_E_LO, _E_HI  = 291, 312   # 4:51-5:12/km  Easy
_M_PACE       = 264         # 4:24/km       Marathon
_T_LO, _T_HI  = 244, 251   # 4:04-4:11/km  Threshold
_I_PACE       = 221         # 3:41/km       Interval
_R_PACE       = 207         # 3:27/km       Repetition

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

# ── パフォーマンスプロフィール & Sub-3 ロードマップ ───────────────────────
def build_performance_profile():
    """VO2Max・VDOT・目標タイム・練習ペース・Sub-3ロードマップ HTML"""

    vdot_pb  = 52   # PB 3:21 ≈ VDOT 52
    vdot_vo2 = _VO2MAX
    vdot_gap = vdot_vo2 - vdot_pb

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
          <div style="position:absolute;left:0;top:30px;font-size:10px;color:#718096">VDOT {vdot_pb}<br>（PB 3:21）</div>
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
          const VDOT_PB=52, VDOT_G1=59, VDOT_ULT=63;

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
              <option value="42.2">フルマラソン ＋42.2km</option>
              <option value="21.1">ハーフマラソン ＋21.1km</option>
              <option value="0">レースなし（走行距離のみ）</option>
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
        # I ペース 3:43/km ± 7秒 (216-230秒) が理想
        if not pace: pace_score = 1.0
        elif 216 <= pace <= 230: pace_score = 2.5   # 3:36-3:50 ◎ I zone
        elif 210 <= pace < 216:  pace_score = 2.0   # やや速いが OK
        elif 230 <= pace <= 250: pace_score = 1.5   # T-pace 寄り
        else:                    pace_score = 0.5
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
            "VO₂max（最大酸素摂取量）の向上が主目的。"
            "<strong>ダニエルズ理論</strong>では I ペース（<strong>3:43/km — VO₂Max 58 基準</strong>）で"
            " 3〜5分間のレップを 5〜6本反復する。"
            " <strong>Pfitzinger</strong> では VO₂max インターバル（600m〜1200m）を"
            " 週1回のみ実施を推奨。Sub-3:00 への最重要セッション。"
        )
        lp_avg, lp_std = lap_pace_stats(laps)
        if pace:
            diff = pace - _I_PACE
            pace_note = "◎ I ペース域" if abs(diff) <= 10 else ("やや速め" if diff < 0 else "やや遅め")
            assessment = f"{dist:.1f}km、平均 {run['pace_per_km']}/km（目標 3:43 比 {'+' if diff>=0 else ''}{diff}秒）{pace_note}、HR {hr:.0f}/{maxhr:.0f}bpm。"
        if lp_std is not None:
            cv = lp_std / lp_avg * 100 if lp_avg else 0
            if cv < 8:
                assessment += f" ラップ変動 ±{lp_std:.0f}秒 — <strong>ペースが安定しています</strong>。"
            else:
                assessment += f" ラップ変動 ±{lp_std:.0f}秒 — 前半突っ込み気味の可能性。次回はイーブンペースを意識してください。"
        if hr >= 175:
            assessment += " 心拍が VO₂max 域に達しており、追い込めています。"
        elif hr >= 168:
            assessment += " 心拍はVO₂max 域に近い。最後のレップで 175+ を狙えると理想的。"
        tips = [
            "【ダニエルズ】I ペース 3:43/km を週1回・総量 8km 以内",
            "【Pfitzinger】1km × 5-6本 r=3分 or 1200m × 4-5本",
            "ウォームアップ 2km（4:53-5:15/km） + クールダウン 2km 必須",
            "80/20 ルール：インターバルは週のうちの「20%強度」に相当。E 走で土台を作ってこそ効果が出る",
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

<div class="header">
  <h1>🏃 {MONTH_LABEL} ランニングレポート</h1>
  <p>Strava データより生成 — {date.today()}</p>
</div>

<div class="container">

  {build_performance_profile()}

  {build_top_plan(runs)}

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

with open(OUTPUT, "w", encoding="utf-8") as f:
    f.write(html)

print(f"✓ {OUTPUT} を生成しました")
print(f"  → open \"{OUTPUT}\"")

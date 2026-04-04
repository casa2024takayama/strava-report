#!/usr/bin/env python3
"""
Strava ランニングデータ取得（現在月を自動検出）
- レート制限をヘッダーで先読みし、上限手前で自動待機
- 取得済みアクティビティはキャッシュから読み込み（2回目以降は API 不使用）
- ランニング種目のみ対象
- 出力: runs_YYYYMM.csv / runs_YYYYMM_laps.csv / gps_streams.csv
環境変数 TARGET_YEAR_MONTH=YYYY-MM で月を指定可能（省略時は当月）
"""

import calendar
import csv
import json
import os
import time
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlencode, urlparse

import requests

# ── .env 読み込み ──────────────────────────────────────────────────────────
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

CLIENT_ID     = os.environ.get("STRAVA_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "")
REDIRECT_PORT = 8765
REDIRECT_URI  = f"http://localhost:{REDIRECT_PORT}/callback"
TOKEN_FILE    = "strava_tokens.json"
CACHE_DIR     = ".strava_cache"
DETAILS_DIR   = os.path.join(CACHE_DIR, "details")
STREAMS_DIR   = os.path.join(CACHE_DIR, "streams")

# ── 対象月の決定（環境変数 or 当月） ──────────────────────────────────────
_ym_env = os.environ.get("TARGET_YEAR_MONTH", "")  # 例: "2026-03"
if _ym_env:
    _target_year  = int(_ym_env.split("-")[0])
    _target_month = int(_ym_env.split("-")[1])
else:
    _today = date.today()
    _target_year  = _today.year
    _target_month = _today.month

TARGET_YEAR  = _target_year
TARGET_MONTH = _target_month
YYYYMM       = f"{TARGET_YEAR}{TARGET_MONTH:02d}"
MONTH_LABEL  = f"{TARGET_YEAR}年{TARGET_MONTH}月"

RUNS_CSV    = f"runs_{YYYYMM}.csv"
LAPS_CSV    = f"runs_{YYYYMM}_laps.csv"
STREAMS_CSV = "gps_streams.csv"

# ── レート制限状態 ─────────────────────────────────────────────────────────
# 読み取り専用エンドポイントの制限: 100 req/15min, 1000 req/day
# ヘッダー: X-ReadRateLimit-Limit / X-ReadRateLimit-Usage（公式仕様）
# 15分リセット: :00 :15 :30 :45（UTC）
# 1日リセット: UTC 0:00
_rate = {"15min_limit": 100, "day_limit": 1000, "15min_used": 0, "day_used": 0}

def _update_rate(headers):
    try:
        # 読み取り専用ヘッダーを優先、なければ全体ヘッダーにフォールバック
        lim_header = (headers.get("X-ReadRateLimit-Limit")
                      or headers.get("X-RateLimit-Limit", "100,1000"))
        use_header = (headers.get("X-ReadRateLimit-Usage")
                      or headers.get("X-RateLimit-Usage", "0,0"))
        lim = lim_header.split(",")
        use = use_header.split(",")
        _rate["15min_limit"] = int(lim[0]);  _rate["day_limit"] = int(lim[1])
        _rate["15min_used"]  = int(use[0]);  _rate["day_used"]  = int(use[1])
        print(f"  [Rate] 15分: {_rate['15min_used']}/{_rate['15min_limit']}  "
              f"1日: {_rate['day_used']}/{_rate['day_limit']}")
    except (ValueError, IndexError):
        pass

def _sleep_until_next_window():
    """次の15分ウィンドウ（UTC :00 :15 :30 :45）まで待機"""
    import calendar as _cal
    utc = time.gmtime()  # UTC 時刻で計算（Strava 仕様）
    next_min = ((utc.tm_min // 15) + 1) * 15
    if next_min >= 60:
        delta = (60 - utc.tm_min) * 60 - utc.tm_sec + 5
    else:
        delta = (next_min - utc.tm_min) * 60 - utc.tm_sec + 5
    print(f"  ⏸ 15分レート上限に近づきました（UTC {utc.tm_hour}:{utc.tm_min:02d}）。"
          f"{delta}秒 待機します...")
    time.sleep(delta)

def api_get(endpoint, access_token, params=None):
    """レート制限を先読みしながら Strava API を叩く"""
    # 15分枠が 90% 消費されていたら先に待つ
    if _rate["15min_used"] >= _rate["15min_limit"] * 0.9:
        _sleep_until_next_window()
    # 1日枠が 95% 消費されていたら中断
    if _rate["day_used"] >= _rate["day_limit"] * 0.95:
        raise RuntimeError(
            f"1日のレート制限に近づきました ({_rate['day_used']}/{_rate['day_limit']})。"
            "明日再実行してください。"
        )
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"https://www.strava.com/api/v3{endpoint}"
    while True:
        resp = requests.get(url, headers=headers, params=params)
        _update_rate(resp.headers)
        if resp.status_code == 429:
            _sleep_until_next_window()
            continue
        resp.raise_for_status()
        return resp.json()

# ── OAuth2 ─────────────────────────────────────────────────────────────────
_auth_code = None

class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _auth_code
        params = parse_qs(urlparse(self.path).query)
        if "code" in params:
            _auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>認証完了！このタブを閉じてください。</h2>".encode())
        else:
            self.send_response(400); self.end_headers()
    def log_message(self, *_): pass

def _authorize():
    global _auth_code
    _auth_code = None
    url = "https://www.strava.com/oauth/authorize?" + urlencode({
        "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
        "response_type": "code", "scope": "read,activity:read_all",
    })
    server = HTTPServer(("localhost", REDIRECT_PORT), _CallbackHandler)
    t = Thread(target=server.handle_request, daemon=True); t.start()
    print("ブラウザで Strava 認証ページを開きます...")
    webbrowser.open(url)
    t.join(timeout=120)
    server.server_close()
    if not _auth_code:
        raise RuntimeError("認証タイムアウト（120秒）")
    resp = requests.post("https://www.strava.com/oauth/token", data={
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "code": _auth_code, "grant_type": "authorization_code",
    })
    resp.raise_for_status()
    tokens = resp.json()
    with open(TOKEN_FILE, "w") as f: json.dump(tokens, f, indent=2)
    print(f"✓ 認証成功: {tokens['athlete']['firstname']} {tokens['athlete']['lastname']}")
    return tokens

def get_tokens():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f: tokens = json.load(f)
        if tokens.get("expires_at", 0) > time.time():
            print("✓ 保存済みトークンを使用")
            return tokens
        print("トークンをリフレッシュ中...")
        resp = requests.post("https://www.strava.com/oauth/token", data={
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
            "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
        })
        resp.raise_for_status()
        tokens = resp.json()
        with open(TOKEN_FILE, "w") as f: json.dump(tokens, f, indent=2)
        return tokens
    return _authorize()

# ── キャッシュ ─────────────────────────────────────────────────────────────
def _cache_path(activity_id):
    return os.path.join(DETAILS_DIR, f"{activity_id}.json")

def _load_cache(activity_id):
    p = _cache_path(activity_id)
    if os.path.exists(p):
        with open(p) as f: return json.load(f)
    return None

def _save_cache(activity_id, data):
    os.makedirs(DETAILS_DIR, exist_ok=True)
    with open(_cache_path(activity_id), "w") as f: json.dump(data, f)

# ── データ取得 ─────────────────────────────────────────────────────────────
RUN_TYPES = {"Run", "TrailRun", "VirtualRun", "Treadmill"}

def fetch_month_runs(access_token, year=None, month=None):
    """指定月（省略時は当月）のランニングアクティビティ一覧を取得"""
    y = year  or TARGET_YEAR
    m = month or TARGET_MONTH
    last_day = calendar.monthrange(y, m)[1]
    after  = int(calendar.timegm(time.strptime(f"{y}-{m:02d}-01",       "%Y-%m-%d")))
    before = int(calendar.timegm(time.strptime(f"{y}-{m:02d}-{last_day}", "%Y-%m-%d"))) + 86400
    runs, page = [], 1
    while True:
        batch = api_get("/athlete/activities", access_token,
                        params={"after": after, "before": before,
                                "per_page": 100, "page": page})
        if not batch: break
        for a in batch:
            if a.get("sport_type") in RUN_TYPES or a.get("type") in RUN_TYPES:
                runs.append(a)
        page += 1
    return runs

def fetch_detail(activity_id, access_token):
    """詳細をキャッシュ優先で取得"""
    cached = _load_cache(activity_id)
    if cached:
        print(f"  (キャッシュ) {activity_id}")
        return cached
    print(f"  (API取得)   {activity_id}")
    detail = api_get(f"/activities/{activity_id}", access_token)
    _save_cache(activity_id, detail)
    return detail

def _stream_cache_path(activity_id):
    return os.path.join(STREAMS_DIR, f"{activity_id}.json")

def fetch_stream(activity_id, access_token):
    """GPS ストリームをキャッシュ優先で取得"""
    p = _stream_cache_path(activity_id)
    if os.path.exists(p):
        print(f"  (キャッシュ) stream {activity_id}")
        with open(p) as f: return json.load(f)
    print(f"  (API取得)   stream {activity_id}")
    keys = "latlng,altitude,distance,time,heartrate,cadence"
    try:
        data = api_get(f"/activities/{activity_id}/streams",
                       access_token,
                       params={"keys": keys, "key_by_type": "true"})
    except Exception as e:
        print(f"  ストリーム取得スキップ: {e}")
        data = {}
    os.makedirs(STREAMS_DIR, exist_ok=True)
    with open(p, "w") as f: json.dump(data, f)
    return data

def export_streams_csv(run_details, streams_map, filename="gps_streams.csv"):
    """GPS ストリームを CSV に出力（3km 以上のランのみ）"""
    fields = ["activity_id", "date", "activity_name",
              "point_index", "lat", "lng", "altitude_m",
              "distance_m", "time_s", "heartrate", "cadence"]
    total = 0
    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for d in run_details:
            aid  = str(d.get("id"))
            dist = (d.get("distance") or 0) / 1000
            if dist < 3: continue
            s    = streams_map.get(aid, {})
            latlng   = (s.get("latlng")   or {}).get("data", [])
            alts     = (s.get("altitude")  or {}).get("data", [])
            dists    = (s.get("distance")  or {}).get("data", [])
            times    = (s.get("time")      or {}).get("data", [])
            hrs      = (s.get("heartrate") or {}).get("data", [])
            cads     = (s.get("cadence")   or {}).get("data", [])
            for i, ll in enumerate(latlng):
                w.writerow({
                    "activity_id":   aid,
                    "date":          d.get("start_date_local", "")[:10],
                    "activity_name": d.get("name"),
                    "point_index":   i,
                    "lat":           ll[0] if len(ll) > 1 else None,
                    "lng":           ll[1] if len(ll) > 1 else None,
                    "altitude_m":    alts[i] if i < len(alts) else None,
                    "distance_m":    round(dists[i], 1) if i < len(dists) else None,
                    "time_s":        times[i] if i < len(times) else None,
                    "heartrate":     hrs[i]  if i < len(hrs)  else None,
                    "cadence":       cads[i] if i < len(cads) else None,
                })
                total += 1
    print(f"✓ {filename}  ({total} ポイント)")

# ── CSV 出力 ───────────────────────────────────────────────────────────────
def _pace(distance_m, moving_time_s):
    """m/s → 分/km 文字列 (例: '5:32')"""
    if not distance_m or not moving_time_s: return ""
    sec_per_km = moving_time_s / (distance_m / 1000)
    m, s = divmod(int(sec_per_km), 60)
    return f"{m}:{s:02d}"

def _hms(seconds):
    h, r = divmod(int(seconds), 3600); m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def export_runs_csv(details, filename=None):
    if filename is None: filename = RUNS_CSV
    fields = [
        "activity_id", "name", "date", "weekday", "sport_type",
        "distance_km", "moving_time", "elapsed_time", "pace_per_km",
        "avg_heartrate", "max_heartrate", "avg_cadence", "avg_watts",
        "elevation_gain_m", "suffer_score", "total_laps",
    ]
    rows = []
    for d in details:
        dt = d.get("start_date_local", "")[:10]
        try:
            wd = ["月","火","水","木","金","土","日"][
                __import__("datetime").date.fromisoformat(dt).weekday()]
        except Exception: wd = ""
        rows.append({
            "activity_id":    d.get("id"),
            "name":           d.get("name"),
            "date":           dt,
            "weekday":        wd,
            "sport_type":     d.get("sport_type"),
            "distance_km":    round((d.get("distance") or 0) / 1000, 3),
            "moving_time":    _hms(d.get("moving_time") or 0),
            "elapsed_time":   _hms(d.get("elapsed_time") or 0),
            "pace_per_km":    _pace(d.get("distance"), d.get("moving_time")),
            "avg_heartrate":  d.get("average_heartrate"),
            "max_heartrate":  d.get("max_heartrate"),
            "avg_cadence":    round((d.get("average_cadence") or 0) * 2) or None,
            "avg_watts":      d.get("average_watts"),
            "elevation_gain_m": d.get("total_elevation_gain"),
            "suffer_score":   d.get("suffer_score"),
            "total_laps":     len(d.get("laps", [])),
        })
    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)
    print(f"✓ {filename}  ({len(rows)} 件)")

def export_laps_csv(details, filename=None):
    if filename is None: filename = LAPS_CSV
    fields = [
        "activity_id", "date", "activity_name",
        "lap_index", "lap_name",
        "distance_km", "moving_time", "pace_per_km",
        "avg_heartrate", "max_heartrate", "avg_cadence", "avg_watts",
        "elevation_gain_m",
    ]
    rows = []
    for d in details:
        aid  = d.get("id")
        aname = d.get("name")
        dt   = d.get("start_date_local", "")[:10]
        for lap in d.get("laps", []):
            rows.append({
                "activity_id":    aid,
                "date":           dt,
                "activity_name":  aname,
                "lap_index":      lap.get("lap_index"),
                "lap_name":       lap.get("name"),
                "distance_km":    round((lap.get("distance") or 0) / 1000, 3),
                "moving_time":    _hms(lap.get("moving_time") or 0),
                "pace_per_km":    _pace(lap.get("distance"), lap.get("moving_time")),
                "avg_heartrate":  lap.get("average_heartrate"),
                "max_heartrate":  lap.get("max_heartrate"),
                "avg_cadence":    round((lap.get("average_cadence") or 0) * 2) or None,
                "avg_watts":      lap.get("average_watts"),
                "elevation_gain_m": lap.get("total_elevation_gain"),
            })
    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)
    print(f"✓ {filename}  ({len(rows)} ラップ)")

# ── PB 自動更新 ────────────────────────────────────────────────────────────
PBS_FILE = "pbs.json"

# Strava best_efforts の name → pbs.json のキー対応
_BE_MAP = {
    "1 mile":        "1mile",
    "5K":            "5km",
    "10K":           "10km",
    "Half-Marathon": "half",
    "Marathon":      "full",
}

def _hms_to_sec(s):
    parts = list(map(int, s.split(":")))
    if len(parts) == 3: return parts[0]*3600 + parts[1]*60 + parts[2]
    if len(parts) == 2: return parts[0]*60 + parts[1]
    return int(parts[0])

def update_pbs(details):
    """全アクティビティの best_efforts を走査して pbs.json を更新"""
    if os.path.exists(PBS_FILE):
        with open(PBS_FILE) as f:
            pbs = json.load(f)
    else:
        pbs = {}

    updated = []
    for d in details:
        aid  = str(d.get("id"))
        dt   = d.get("start_date_local", "")[:10]
        for be in d.get("best_efforts", []):
            key = _BE_MAP.get(be.get("name"))
            if not key: continue
            new_sec = be.get("moving_time")
            if not new_sec: continue
            old_sec = pbs.get(key, {}).get("time_sec", 999999)
            if new_sec < old_sec:
                h, r = divmod(new_sec, 3600); m, s = divmod(r, 60)
                time_str = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
                pbs[key] = {
                    "time_sec":   new_sec,
                    "time_str":   time_str,
                    "activity_id": aid,
                    "date":       dt,
                    "source":     "best_efforts",
                }
                updated.append(f"  🏅 {key}: {time_str}（{dt}）")

    with open(PBS_FILE, "w") as f:
        json.dump(pbs, f, indent=2, ensure_ascii=False)

    if updated:
        print("\n🎉 PB更新！")
        for u in updated: print(u)
    else:
        print("  PB更新なし")
    return pbs

# ── メイン ─────────────────────────────────────────────────────────────────
def main():
    global CLIENT_ID, CLIENT_SECRET
    if not CLIENT_ID or not CLIENT_SECRET:
        CLIENT_ID     = input("Client ID: ").strip()
        CLIENT_SECRET = input("Client Secret: ").strip()

    print(f"\n=== Strava {MONTH_LABEL} ランデータ取得 ===\n")
    tokens = get_tokens()
    access_token = tokens["access_token"]

    print(f"\n[1/3] {MONTH_LABEL}のランニングを検索中...")
    runs = fetch_month_runs(access_token)
    print(f"  → {len(runs)} 件のランニングを発見")

    print("\n[2/4] 詳細を取得（キャッシュ優先）...")
    details = []
    for i, run in enumerate(runs, 1):
        print(f"  ({i}/{len(runs)}) {run.get('name')}")
        details.append(fetch_detail(run["id"], access_token))

    print("\n[3/4] GPS ストリームを取得（3km 以上・キャッシュ優先）...")
    streams_map = {}
    targets = [d for d in details if (d.get("distance") or 0) / 1000 >= 3]
    for i, d in enumerate(targets, 1):
        aid = str(d["id"])
        print(f"  ({i}/{len(targets)}) {d.get('name')}")
        streams_map[aid] = fetch_stream(aid, access_token)

    print("\n[4/5] PB チェック・更新...")
    update_pbs(details)

    print("\n[5/5] CSV 出力...")
    export_runs_csv(details)
    export_laps_csv(details)
    export_streams_csv(details, streams_map)

    print("\n=== 完了 ===")
    print(f"  {RUNS_CSV}  - アクティビティ一覧")
    print(f"  {LAPS_CSV}  - ラップ詳細")
    print("  gps_streams.csv          - GPS ストリーム（3km 以上）")
    print("\n次のステップ:")
    print("  python3 report_html.py  # HTML レポート生成")

if __name__ == "__main__":
    main()

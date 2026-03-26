#!/usr/bin/env python3
"""
Strava Data Exporter
アクティビティ一覧・詳細・GPSルートデータをCSVに出力します
"""

import csv
import json
import os
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlencode, urlparse

import requests

# .env ファイルを自動読み込み
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

# ─── 設定 ──────────────────────────────────────────────────────────────────
CLIENT_ID = os.environ.get("STRAVA_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "")
REDIRECT_PORT = 8765
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"
TOKEN_FILE = "strava_tokens.json"

# ─── OAuth2 ────────────────────────────────────────────────────────────────

auth_code = None


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if "code" in params:
            auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                "<html><body><h2>認証完了！このタブを閉じてください。</h2></body></html>".encode()
            )
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # ログ非表示


def get_tokens():
    """トークンをファイルから読み込むか、OAuth2フローで取得する"""
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            tokens = json.load(f)
        # アクセストークンの有効期限チェック
        if tokens.get("expires_at", 0) > time.time():
            print("✓ 保存済みトークンを使用します")
            return tokens
        # リフレッシュ
        print("トークンを更新中...")
        resp = requests.post(
            "https://www.strava.com/oauth/token",
            data={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
            },
        )
        resp.raise_for_status()
        tokens = resp.json()
        save_tokens(tokens)
        return tokens

    # 新規認証
    return authorize()


def authorize():
    global auth_code
    auth_code = None

    scope = "read,activity:read_all"
    auth_url = (
        "https://www.strava.com/oauth/authorize?"
        + urlencode(
            {
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
                "response_type": "code",
                "scope": scope,
            }
        )
    )

    # ローカルサーバー起動
    server = HTTPServer(("localhost", REDIRECT_PORT), CallbackHandler)
    thread = Thread(target=server.handle_request)
    thread.daemon = True
    thread.start()

    print(f"\nブラウザでStravaの認証ページを開きます...")
    webbrowser.open(auth_url)
    print("認証を完了してください。ブラウザで許可を押してください。\n")

    thread.join(timeout=120)
    server.server_close()

    if not auth_code:
        raise RuntimeError("認証タイムアウト（120秒）")

    resp = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": auth_code,
            "grant_type": "authorization_code",
        },
    )
    resp.raise_for_status()
    tokens = resp.json()
    save_tokens(tokens)
    print(f"✓ 認証成功: {tokens['athlete']['firstname']} {tokens['athlete']['lastname']}")
    return tokens


def save_tokens(tokens):
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)


# ─── Strava API ────────────────────────────────────────────────────────────


def api_get(endpoint, access_token, params=None):
    """Strava API GETリクエスト（レート制限対応）"""
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"https://www.strava.com/api/v3{endpoint}"
    while True:
        resp = requests.get(url, headers=headers, params=params)
        if resp.status_code == 429:
            wait = int(resp.headers.get("X-RateLimit-Reset", 60))
            print(f"  レート制限: {wait}秒待機中...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()


def fetch_all_activities(access_token, after=None, before=None):
    """アクティビティを取得（ページネーション対応）"""
    activities = []
    page = 1
    while True:
        params = {"per_page": 100, "page": page}
        if after:
            params["after"] = after
        if before:
            params["before"] = before
        batch = api_get("/athlete/activities", access_token, params=params)
        if not batch:
            break
        activities.extend(batch)
        print(f"  取得済み: {len(activities)} 件...")
        page += 1
    return activities


def fetch_activity_detail(activity_id, access_token):
    """アクティビティ詳細（ラップ含む）を取得"""
    return api_get(f"/activities/{activity_id}", access_token)


def fetch_streams(activity_id, access_token):
    """GPSストリームデータを取得"""
    keys = "time,latlng,altitude,heartrate,cadence,watts,velocity_smooth,distance"
    try:
        data = api_get(
            f"/activities/{activity_id}/streams",
            access_token,
            params={"keys": keys, "key_by_type": "true"},
        )
        return data
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return {}
        raise


# ─── CSV出力 ───────────────────────────────────────────────────────────────


def export_activities_csv(activities, filename="activities.csv"):
    """アクティビティ一覧をCSVに出力"""
    if not activities:
        print("アクティビティなし")
        return

    fieldnames = [
        "id", "name", "sport_type", "start_date_local",
        "distance_km", "moving_time_min", "elapsed_time_min",
        "total_elevation_gain_m", "average_speed_kmh", "max_speed_kmh",
        "average_heartrate", "max_heartrate", "average_watts",
        "calories", "suffer_score", "kudos_count", "achievement_count",
        "start_latlng", "city", "country",
    ]

    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for a in activities:
            writer.writerow({
                "id": a.get("id"),
                "name": a.get("name"),
                "sport_type": a.get("sport_type"),
                "start_date_local": a.get("start_date_local"),
                "distance_km": round(a.get("distance", 0) / 1000, 3),
                "moving_time_min": round(a.get("moving_time", 0) / 60, 1),
                "elapsed_time_min": round(a.get("elapsed_time", 0) / 60, 1),
                "total_elevation_gain_m": a.get("total_elevation_gain"),
                "average_speed_kmh": round((a.get("average_speed") or 0) * 3.6, 2),
                "max_speed_kmh": round((a.get("max_speed") or 0) * 3.6, 2),
                "average_heartrate": a.get("average_heartrate"),
                "max_heartrate": a.get("max_heartrate"),
                "average_watts": a.get("average_watts"),
                "calories": a.get("calories"),
                "suffer_score": a.get("suffer_score"),
                "kudos_count": a.get("kudos_count"),
                "achievement_count": a.get("achievement_count"),
                "start_latlng": str(a.get("start_latlng", "")),
                "city": a.get("location_city"),
                "country": a.get("location_country"),
            })
    print(f"✓ {filename} ({len(activities)} 件)")


def export_activity_details_csv(activities, access_token, filename="activity_details.csv"):
    """アクティビティ詳細（ラップ含む）をCSVに出力"""
    rows = []
    for i, a in enumerate(activities):
        aid = a["id"]
        print(f"  詳細取得中 ({i+1}/{len(activities)}): {a.get('name')} [{aid}]")
        try:
            detail = fetch_activity_detail(aid, access_token)
        except Exception as e:
            print(f"  スキップ: {e}")
            continue

        for lap in detail.get("laps", [{}]):
            rows.append({
                "activity_id": aid,
                "activity_name": detail.get("name"),
                "sport_type": detail.get("sport_type"),
                "start_date_local": detail.get("start_date_local"),
                "lap_index": lap.get("lap_index", 0),
                "lap_name": lap.get("name"),
                "lap_distance_km": round((lap.get("distance") or 0) / 1000, 3),
                "lap_moving_time_min": round((lap.get("moving_time") or 0) / 60, 2),
                "lap_elapsed_time_min": round((lap.get("elapsed_time") or 0) / 60, 2),
                "lap_avg_speed_kmh": round((lap.get("average_speed") or 0) * 3.6, 2),
                "lap_max_speed_kmh": round((lap.get("max_speed") or 0) * 3.6, 2),
                "lap_avg_heartrate": lap.get("average_heartrate"),
                "lap_max_heartrate": lap.get("max_heartrate"),
                "lap_avg_watts": lap.get("average_watts"),
                "lap_elevation_gain_m": lap.get("total_elevation_gain"),
            })
        if not detail.get("laps"):
            rows.append({
                "activity_id": aid,
                "activity_name": detail.get("name"),
                "sport_type": detail.get("sport_type"),
                "start_date_local": detail.get("start_date_local"),
            })

    if not rows:
        return

    fieldnames = list(rows[0].keys())
    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"✓ {filename} ({len(rows)} ラップ)")


def export_gps_streams_csv(activities, access_token, filename="gps_streams.csv"):
    """GPSストリームデータをCSVに出力"""
    fieldnames = [
        "activity_id", "activity_name", "sport_type", "start_date_local",
        "point_index", "time_s", "lat", "lng", "altitude_m",
        "distance_m", "velocity_kmh", "heartrate", "cadence", "watts",
    ]

    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        total_points = 0

        for i, a in enumerate(activities):
            aid = a["id"]
            print(f"  GPS取得中 ({i+1}/{len(activities)}): {a.get('name')} [{aid}]")
            try:
                streams = fetch_streams(aid, access_token)
            except Exception as e:
                print(f"  スキップ: {e}")
                continue

            latlng = (streams.get("latlng") or {}).get("data", [])
            times = (streams.get("time") or {}).get("data", [])
            alts = (streams.get("altitude") or {}).get("data", [])
            dists = (streams.get("distance") or {}).get("data", [])
            vels = (streams.get("velocity_smooth") or {}).get("data", [])
            hrs = (streams.get("heartrate") or {}).get("data", [])
            cads = (streams.get("cadence") or {}).get("data", [])
            watts = (streams.get("watts") or {}).get("data", [])

            n = len(latlng)
            for j in range(n):
                ll = latlng[j] if j < len(latlng) else [None, None]
                writer.writerow({
                    "activity_id": aid,
                    "activity_name": a.get("name"),
                    "sport_type": a.get("sport_type"),
                    "start_date_local": a.get("start_date_local"),
                    "point_index": j,
                    "time_s": times[j] if j < len(times) else None,
                    "lat": ll[0],
                    "lng": ll[1],
                    "altitude_m": alts[j] if j < len(alts) else None,
                    "distance_m": round(dists[j], 1) if j < len(dists) else None,
                    "velocity_kmh": round(vels[j] * 3.6, 2) if j < len(vels) else None,
                    "heartrate": hrs[j] if j < len(hrs) else None,
                    "cadence": cads[j] if j < len(cads) else None,
                    "watts": watts[j] if j < len(watts) else None,
                })
            total_points += n

    print(f"✓ {filename} ({total_points} ポイント)")


# ─── メイン ────────────────────────────────────────────────────────────────


def main():
    global CLIENT_ID, CLIENT_SECRET
    if not CLIENT_ID or not CLIENT_SECRET:
        print("環境変数を設定してください:")
        print("  export STRAVA_CLIENT_ID=your_client_id")
        print("  export STRAVA_CLIENT_SECRET=your_client_secret")
        print()
        cid = input("Client ID: ").strip()
        cs = input("Client Secret: ").strip()
        os.environ["STRAVA_CLIENT_ID"] = cid
        os.environ["STRAVA_CLIENT_SECRET"] = cs
        CLIENT_ID = cid
        CLIENT_SECRET = cs

    print("\n=== Strava データエクスポート（2026年3月分）===\n")

    # 既に全ファイルが揃っていればスキップ
    output_files = ["activities.csv", "activity_details.csv", "gps_streams.csv"]
    if all(os.path.exists(f) for f in output_files):
        print("✓ 既に取得済みです。CSVファイルを再利用します。")
        print("\n出力ファイル:")
        for f in output_files:
            print(f"  {f}")
        return

    import calendar
    after  = int(calendar.timegm(time.strptime("2026-03-01", "%Y-%m-%d")))
    before = int(calendar.timegm(time.strptime("2026-04-01", "%Y-%m-%d")))

    tokens = get_tokens()
    access_token = tokens["access_token"]

    print("\n[1/4] 2026年3月のアクティビティを取得中...")
    activities = fetch_all_activities(access_token, after=after, before=before)
    print(f"  合計 {len(activities)} 件のアクティビティ")

    print("\n[2/4] アクティビティ一覧をCSVに出力...")
    export_activities_csv(activities)

    print("\n[3/4] アクティビティ詳細（ラップ）をCSVに出力...")
    export_activity_details_csv(activities, access_token)

    print("\n[4/4] GPSストリームデータをCSVに出力...")
    export_gps_streams_csv(activities, access_token)

    print("\n=== 完了 ===")
    print("出力ファイル:")
    print("  activities.csv       - アクティビティ一覧")
    print("  activity_details.csv - アクティビティ詳細（ラップ）")
    print("  gps_streams.csv      - GPSルートデータ")


if __name__ == "__main__":
    main()

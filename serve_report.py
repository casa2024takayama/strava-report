#!/usr/bin/env python3
"""
Strava レポート用ローカル HTTP サーバー

- 静的 HTML / CSV を配信（http://127.0.0.1:8766/）
- POST /api/update  → strava_fetch.py + report_html.py
- POST /api/coach   → coach_claude.py（既定）/ coach_gemini.py / coach_ollama.py + report_html.py
- GET  /api/status  → ジョブ進捗

使い方:
  python3 serve_report.py
  python3 serve_report.py --open
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import webbrowser
from datetime import date, datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from urllib.request import urlopen

ROOT = os.path.dirname(os.path.abspath(__file__))
VENV_PYTHON = os.path.join(ROOT, ".venv", "bin", "python3")
REQUIREMENTS = os.path.join(ROOT, "requirements.txt")
PORT = int(os.environ.get("REPORT_SERVER_PORT", "8766"))
os.environ.setdefault("REPORT_EDITION", "local")


def _ensure_venv() -> None:
    """ローカル用 .venv（requests 等）を用意し、必要なら自身を venv Python で再起動。"""
    _ensure_venv_ready()
    if os.path.isfile(VENV_PYTHON) and os.path.realpath(sys.executable) != os.path.realpath(VENV_PYTHON):
        print("↻ venv の Python で再起動します…", flush=True)
        os.execv(VENV_PYTHON, [VENV_PYTHON, *sys.argv])


def _ensure_venv_ready() -> str:
    """venv と依存パッケージを用意し、ジョブ実行用 Python のパスを返す。"""
    if not os.path.isfile(REQUIREMENTS):
        return sys.executable
    if not os.path.isfile(VENV_PYTHON):
        print("▶ 初回セットアップ: .venv を作成しています…")
        subprocess.check_call([sys.executable, "-m", "venv", os.path.join(ROOT, ".venv")])
        subprocess.check_call([VENV_PYTHON, "-m", "pip", "install", "-q", "-r", REQUIREMENTS])
        print("✓ .venv 準備完了")
    return VENV_PYTHON


def _python() -> str:
    return _ensure_venv_ready()

_lock = threading.Lock()
_state: dict = {
    "kind": None,
    "running": False,
    "step": "",
    "log": [],
    "error": None,
    "done": False,
    "last_fetch": None,
    "last_coach": None,
}


def _current_month_arg() -> str:
    ym = os.environ.get("TARGET_YEAR_MONTH", "").strip()
    if ym:
        return ym
    today = date.today()
    return f"{today.year}-{today.month:02d}"


def _read_meta(filename: str, key: str = "label") -> str | None:
    path = os.path.join(ROOT, ".strava_cache", filename)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get(key):
            return data[key]
        from datetime import datetime

        return datetime.fromisoformat(data["at"]).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return None


def _snapshot() -> dict:
    with _lock:
        return {
            "kind": _state.get("kind"),
            "running": _state["running"],
            "step": _state["step"],
            "log": list(_state["log"][-80:]),
            "error": _state["error"],
            "done": _state["done"],
            "last_fetch": _state.get("last_fetch") or _read_meta("last_fetch.json"),
            "last_coach": _state.get("last_coach") or _read_meta("last_coach.json"),
        }


def _append_log(line: str) -> None:
    # コンソール（サーバを起動したターミナル）にも出力＝HTML をリロードせず進捗が見える
    print(f"[{datetime.now():%H:%M:%S}] {line}", flush=True)
    with _lock:
        _state["log"].append(line)
        if len(_state["log"]) > 200:
            _state["log"] = _state["log"][-200:]


def _run_script(step: str, argv: list[str]) -> None:
    with _lock:
        _state["step"] = step
    script_name = os.path.basename(argv[1]) if len(argv) > 1 else argv[0]
    _append_log(f"▶ {script_name} 開始")
    proc = subprocess.Popen(
        argv,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,  # 行バッファ
        env={**os.environ, "PYTHONUNBUFFERED": "1"},  # 子プロセスの print を即時フラッシュ
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        _append_log(line.rstrip())
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"{script_name} が終了コード {code} で失敗しました")
    _append_log(f"✓ {script_name} 完了")


def _run_job(kind: str, steps: list[tuple[str, list[str]]]) -> None:
    try:
        for step, argv in steps:
            _run_script(step, argv)
        with _lock:
            _state["done"] = True
            _state["running"] = False
            _state["step"] = "done"
            if kind == "fetch":
                _state["last_fetch"] = _read_meta("last_fetch.json")
                _state["last_coach"] = _read_meta("last_coach.json")
                if _state["last_fetch"]:
                    _append_log(f"✓ データ取得完了 — {_state['last_fetch']}")
                if _state["last_coach"]:
                    _append_log(f"✓ AI 評価 — {_state['last_coach']}")
            elif kind == "coach":
                _state["last_coach"] = _read_meta("last_coach.json")
                if _state["last_coach"]:
                    _append_log(f"✓ AI コーチング完了 — {_state['last_coach']}")
            elif kind == "garmin":
                _append_log("✓ Garmin 取得・レポート再生成 完了")
    except Exception as exc:
        with _lock:
            _state["error"] = str(exc)
            _state["running"] = False
            _state["step"] = "error"
        _append_log(f"❌ {exc}")


def _start_job(kind: str, steps: list[tuple[str, list[str]]]) -> dict:
    with _lock:
        if _state["running"]:
            kind_label = {"fetch": "データ取得", "garmin": "Garmin 取得"}.get(kind, "AI 評価")
            return {
                "started": False,
                "reason": "already_running",
                "message": f"{kind_label}はすでに実行中です。完了までお待ちください。",
            }
        _state.clear()
        _state.update(
            kind=kind,
            running=True,
            step="starting",
            log=[],
            error=None,
            done=False,
            last_fetch=None,
            last_coach=None,
        )
    _label = {"fetch": "データ更新（Strava＋AI）", "coach": "AI 評価",
              "garmin": "Garmin 取得"}.get(kind, kind)
    print(f"\n─── ▶ {_label} 開始 [{datetime.now():%H:%M:%S}] ───", flush=True)
    threading.Thread(target=_run_job, args=(kind, steps), daemon=True).start()
    return {"started": True}


def start_update() -> dict:
    py = _python()
    month = _current_month_arg()
    coach_argv = [py, _coach_script(), "--month", month]
    if _coach_script() == "coach_ollama.py":
        coach_argv.append("--no-stream")
    return _start_job(
        "fetch",
        [
            ("fetch", [py, "strava_fetch.py"]),
            ("coach", coach_argv),
            ("html", [py, "report_html.py"]),
        ],
    )


def _coach_script() -> str:
    backend = os.environ.get("COACH_BACKEND", "claude").strip().lower()
    if backend == "ollama":
        return "coach_ollama.py"
    if backend == "gemini":
        return "coach_gemini.py"
    return "coach_claude.py"


def start_coach() -> dict:
    py = _python()
    month = _current_month_arg()
    coach_script = _coach_script()
    argv = [py, coach_script, "--month", month]
    if coach_script == "coach_ollama.py":
        argv.append("--no-stream")
    return _start_job(
        "coach",
        [
            ("coach", argv),
            ("html", [py, "report_html.py"]),
        ],
    )


# Garmin 取得（別フォルダの専用 venv で garmin_fetch.py を実行 → レポート再生成）
GARMIN_DIR = os.path.expanduser("~/Desktop/GarminConnect")
GARMIN_PY = os.path.join(GARMIN_DIR, ".venv", "bin", "python3")
GARMIN_SCRIPT = os.path.join(GARMIN_DIR, "garmin_fetch.py")


def start_garmin() -> dict:
    py = _python()
    if not (os.path.exists(GARMIN_PY) and os.path.exists(GARMIN_SCRIPT)):
        return {
            "started": False,
            "reason": "garmin_missing",
            "message": "Garmin 取得スクリプトが見つかりません（~/Desktop/GarminConnect）",
        }
    return _start_job(
        "garmin",
        [
            ("garmin", [GARMIN_PY, GARMIN_SCRIPT, "--days", "35"]),
            ("html", [py, "report_html.py"]),
        ],
    )


class ReportHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        pass

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/status":
            self._send_json(200, _snapshot())
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/update":
            self._send_json(200, start_update())
            return
        if path == "/api/coach":
            self._send_json(200, start_coach())
            return
        if path == "/api/garmin":
            self._send_json(200, start_garmin())
            return
        self.send_error(404)


def _report_url() -> str:
    return f"http://127.0.0.1:{PORT}/index.html"


def _server_already_running() -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{PORT}/api/status", timeout=1) as resp:
            return resp.status == 200
    except OSError:
        return False


def main() -> None:
    _ensure_venv()
    open_browser = "--open" in sys.argv
    url = _report_url()

    if _server_already_running():
        print(f"✓ レポートサーバーは既に起動中です: {url}")
        print("   停止: lsof -ti :8766 | xargs kill")
        if open_browser:
            webbrowser.open(url)
        return

    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), ReportHandler)
    except OSError as e:
        if e.errno == 48:  # Address already in use
            print(f"⚠️ ポート {PORT} は使用中です（別プロセスの可能性）")
            print(f"   既に Strava サーバーなら {url} を開いてください")
            print(f"   停止: lsof -ti :{PORT} | xargs kill")
            if open_browser and _server_already_running():
                webbrowser.open(url)
            sys.exit(1)
        raise

    print(f"🏃 Strava レポートサーバー: {url}")
    print("   Ctrl+C で停止")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました")
        server.server_close()


if __name__ == "__main__":
    main()

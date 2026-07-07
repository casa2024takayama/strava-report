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

対話キー（TTY のとき）:
  q  終了
  r  サーバー再起動
  o  ブラウザでレポートを開く
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import termios
import threading
import tty
import webbrowser
from datetime import date, datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from urllib.request import urlopen

ROOT = os.path.dirname(os.path.abspath(__file__))
VENV_PYTHON = os.path.join(ROOT, ".venv", "bin", "python3")
REQUIREMENTS = os.path.join(ROOT, "requirements.txt")
ENV_FILE = os.path.join(ROOT, ".env")

# ── .env 読み込み ──────────────────────────────────────────────────────────
if os.path.exists(ENV_FILE):
    with open(ENV_FILE) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

PORT = int(os.environ.get("REPORT_SERVER_PORT", "8766"))
os.environ.setdefault("REPORT_EDITION", "local")


def _resolve_host() -> str:
    """バインド先ホストを決定する（既定は127.0.0.1で現状維持、Tailscale利用時のみ opt-in）。"""
    raw = os.environ.get("REPORT_SERVER_HOST", "127.0.0.1").strip()
    if raw in ("", "127.0.0.1"):
        return "127.0.0.1"
    if raw == "auto":
        try:
            result = subprocess.run(
                ["tailscale", "ip", "-4"],
                capture_output=True, text=True, timeout=5, check=True,
            )
            ip = result.stdout.strip().splitlines()[0].strip()
            if ip:
                return ip
        except (OSError, subprocess.SubprocessError, IndexError) as e:
            print(f"⚠️ Tailscale IPの取得に失敗（{e}）— 127.0.0.1にフォールバック", flush=True)
        return "127.0.0.1"
    return raw


HOST = _resolve_host()
TOKEN = ""  # main() で _ensure_token() の結果が入る


def _ensure_token() -> str:
    """API保護用のトークンを .env から読むか、無ければ生成して .env に追記する。"""
    token = os.environ.get("REPORT_SERVER_TOKEN", "").strip()
    if token:
        return token
    token = secrets.token_urlsafe(24)
    # 既存の .env が改行で終わっていない場合、前の行に連結して破損させないよう補正する
    needs_newline = False
    if os.path.exists(ENV_FILE) and os.path.getsize(ENV_FILE) > 0:
        with open(ENV_FILE, "rb") as f:
            f.seek(-1, os.SEEK_END)
            needs_newline = f.read(1) != b"\n"
    with open(ENV_FILE, "a") as f:
        if needs_newline:
            f.write("\n")
        f.write(f"REPORT_SERVER_TOKEN={token}\n")
    os.environ["REPORT_SERVER_TOKEN"] = token
    print("✓ REPORT_SERVER_TOKEN を新規生成し .env に保存しました", flush=True)
    return token


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


def _previous_month_arg(from_month: str | None = None) -> str:
    ym = from_month or _current_month_arg()
    y, m = map(int, ym.split("-", 1))
    if m == 1:
        return f"{y - 1}-12"
    return f"{y}-{m - 1:02d}"


def _months_to_fetch() -> list[str]:
    """当月＋前月（月末ランの取りこぼし防止）。"""
    cur = _current_month_arg()
    return [_previous_month_arg(cur), cur]


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


def _run_script(step: str, argv: list[str], extra_env: dict[str, str] | None = None) -> None:
    with _lock:
        _state["step"] = step
    script_name = os.path.basename(argv[1]) if len(argv) > 1 else argv[0]
    month_note = ""
    if extra_env and extra_env.get("TARGET_YEAR_MONTH"):
        month_note = f" ({extra_env['TARGET_YEAR_MONTH']})"
    _append_log(f"▶ {script_name}{month_note} 開始")
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    if extra_env:
        env.update(extra_env)
    proc = subprocess.Popen(
        argv,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,  # 行バッファ
        env=env,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        _append_log(line.rstrip())
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"{script_name} が終了コード {code} で失敗しました")
    _append_log(f"✓ {script_name} 完了")


def _run_job(kind: str, steps: list[tuple[str, list[str], dict[str, str] | None]]) -> None:
    try:
        for step, argv, extra_env in steps:
            _run_script(step, argv, extra_env)
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


def _start_job(kind: str, steps: list[tuple[str, list[str], dict[str, str] | None]]) -> dict:
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
    months = _months_to_fetch()
    steps: list[tuple[str, list[str], dict[str, str] | None]] = []
    for ym in months:
        steps.append(("fetch", [py, "strava_fetch.py"], {"TARGET_YEAR_MONTH": ym}))
    for ym in months:
        coach_argv = [py, _coach_script(), "--month", ym]
        if _coach_script() == "coach_ollama.py":
            coach_argv.append("--no-stream")
        steps.append(("coach", coach_argv, None))
    for ym in months:
        steps.append(
            ("html", [py, "report_html.py"], {"TARGET_YEAR_MONTH": ym, "REPORT_EDITION": "local"}),
        )
    return _start_job("fetch", steps)


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
            ("coach", argv, None),
            ("html", [py, "report_html.py"], {"REPORT_EDITION": "local"}),
        ],
    )


# Garmin 取得（別フォルダの専用 venv で garmin_fetch.py を実行 → レポート再生成）
GARMIN_DIR = os.path.expanduser("~/GarminConnect")
if not os.path.isdir(GARMIN_DIR):  # 旧場所フォールバック
    GARMIN_DIR = os.path.expanduser("~/Desktop/GarminConnect")
GARMIN_PY = os.path.join(GARMIN_DIR, ".venv", "bin", "python3")
GARMIN_SCRIPT = os.path.join(GARMIN_DIR, "garmin_fetch.py")


def start_garmin() -> dict:
    py = _python()
    if not (os.path.exists(GARMIN_PY) and os.path.exists(GARMIN_SCRIPT)):
        return {
            "started": False,
            "reason": "garmin_missing",
            "message": "Garmin 取得スクリプトが見つかりません（~/GarminConnect）",
        }
    return _start_job(
        "garmin",
        [
            ("garmin", [GARMIN_PY, GARMIN_SCRIPT, "--days", "35"], None),
            ("html", [py, "report_html.py"], {"REPORT_EDITION": "local"}),
        ],
    )


class ReportHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        pass

    def end_headers(self) -> None:
        # ブラウザキャッシュで古いレポートが表示されるのを防ぐ（ローカル閲覧用途）
        self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        return secrets.compare_digest(self.headers.get("X-Report-Token", ""), TOKEN)

    @staticmethod
    def _serve_allowed(path: str) -> bool:
        # ROOT直下には .env / strava_tokens.json / garmin_daily.csv 等の機密が
        # 実在するため、配信はレポート関連ファイルのみに限定する（許可リスト方式）。
        name = path.lstrip("/")
        if name == "":
            return True  # → index.html
        if "/" in name:
            return False  # サブディレクトリは配信しない
        return name.endswith(".html") or name in ("pbs.json", "races.json")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/status":
            if not self._authorized():
                self.send_error(401)
                return
            self._send_json(200, _snapshot())
            return
        if not self._serve_allowed(path):
            self.send_error(404)
            return
        super().do_GET()

    def do_HEAD(self) -> None:
        path = urlparse(self.path).path
        if not self._serve_allowed(path):
            self.send_error(404)
            return
        super().do_HEAD()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path in ("/api/update", "/api/coach", "/api/garmin"):
            if not self._authorized():
                self.send_error(401)
                return
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
    return f"http://{HOST}:{PORT}/index.html"


class ReportServer:
    def __init__(self) -> None:
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.server is not None:
            return
        try:
            self.server = ThreadingHTTPServer((HOST, PORT), ReportHandler)
        except OSError:
            self.server = None
            self.thread = None
            raise
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.server is None:
            return
        self.server.shutdown()
        self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=5)
        self.server = None
        self.thread = None

    def restart(self) -> None:
        with _lock:
            job_running = _state.get("running", False)
        if job_running:
            print("⚠️  ジョブ実行中ですがサーバーを再起動します（ジョブはバックグラウンドで継続）", flush=True)
        print("↻ サーバーを再起動しています…", flush=True)
        self.stop()
        self.start()
        print(f"✓ 再起動完了: {_report_url()}", flush=True)


def _read_key() -> str:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _print_shortcuts() -> None:
    print("   q 終了  |  r 再起動  |  o ブラウザを開く", flush=True)


def _interactive_loop(report_server: ReportServer, url: str) -> None:
    _print_shortcuts()
    try:
        while True:
            key = _read_key()
            if key in ("q", "Q", "\x03"):  # q or Ctrl+C
                print("\n停止しました", flush=True)
                report_server.stop()
                return
            if key in ("r", "R"):
                print("", flush=True)
                report_server.restart()
                _print_shortcuts()
                continue
            if key in ("o", "O"):
                print("\n🌐 ブラウザを開きます…", flush=True)
                webbrowser.open(url)
                _print_shortcuts()
                continue
            if key in ("\r", "\n"):
                continue
    except KeyboardInterrupt:
        print("\n停止しました", flush=True)
        report_server.stop()


def _server_already_running() -> bool:
    from urllib.request import Request

    try:
        req = Request(f"http://{HOST}:{PORT}/api/status", headers={"X-Report-Token": TOKEN})
        with urlopen(req, timeout=1) as resp:
            return resp.status == 200
    except OSError:
        return False


def main() -> None:
    global TOKEN
    _ensure_venv()
    TOKEN = _ensure_token()
    open_browser = "--open" in sys.argv
    url = _report_url()

    if _server_already_running():
        print(f"✓ レポートサーバーは既に起動中です: {url}")
        print("   停止: lsof -ti :8766 | xargs kill")
        if open_browser:
            webbrowser.open(url)
        return

    # ディスク上のHTMLは online版(空トークン)や旧トークンで焼かれている可能性がある
    # (git pull直後・トークン変更後など)。起動前に毎回焼き直して不整合による401を防ぐ。
    print("↻ トークンをHTMLに反映するため report_html.py を実行します…", flush=True)
    subprocess.run([_python(), "report_html.py"], cwd=ROOT, env={**os.environ}, check=False)

    report_server = ReportServer()
    try:
        report_server.start()
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
    if open_browser:
        webbrowser.open(url)

    interactive = sys.stdin.isatty() and not os.environ.get("CI")
    if interactive:
        _interactive_loop(report_server, url)
    else:
        print("   Ctrl+C で停止", flush=True)
        try:
            while report_server.thread is not None and report_server.thread.is_alive():
                report_server.thread.join(timeout=3600)
        except KeyboardInterrupt:
            print("\n停止しました", flush=True)
            report_server.stop()


if __name__ == "__main__":
    main()

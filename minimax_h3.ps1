# ─────────────────────────────────────────────────────────────────
# minimax_h3.ps1 — MiniMax H3（ComfyUI 動画生成）をワンコマンドで起動（Windows 版）
#
#   1) ComfyUI のインストール先を自動検出（$env:COMFYUI_DIR で上書き可）
#      ・ポータブル版（ComfyUI_windows_portable）にも対応
#   2) MiniMax H3 のモデルファイルが揃っているか確認（無ければ案内）
#   3) すでにサーバーが動いていればブラウザを開くだけ
#   4) 動いていなければ別ウィンドウで起動 → 準備完了を待ってブラウザを開く
#
# 使い方（PowerShell）:
#   .\minimax_h3.ps1            # 起動してブラウザを開く
#   .\minimax_h3.ps1 -Update    # ComfyUI を更新してから起動
#   .\minimax_h3.ps1 -Lan       # 同じ家の Mac 等からもアクセス可能にする
#   .\minimax_h3.ps1 -NoBrowser # ブラウザを開かない（リモート起動用）
#   （エクスプローラーからは minimax_h3.bat をダブルクリック）
#
# 環境変数:
#   COMFYUI_DIR   ComfyUI のパス（省略時は自動検出）
#   COMFYUI_PORT  ポート（既定 8188）
#   COMFYUI_ARGS  ComfyUI への追加引数（例: "--force-fp16"）
#
# 詳細・初回セットアップは docs/minimax-h3.md を参照。
# ─────────────────────────────────────────────────────────────────
param(
    [switch]$Update,     # ComfyUI を更新してから起動
    [switch]$Lan,        # LAN / Tailscale からアクセス可能にする（0.0.0.0 で listen）
    [switch]$NoBrowser   # ブラウザを開かない（Mac からのリモート起動用）
)

$ErrorActionPreference = "Stop"
$Port   = if ($env:COMFYUI_PORT) { $env:COMFYUI_PORT } else { "8188" }
$Listen = if ($Lan) { "0.0.0.0" } else { "127.0.0.1" }
$Url    = "http://127.0.0.1:$Port"

function Test-ComfyAlive {
    try {
        Invoke-WebRequest -Uri "$Url/system_stats" -UseBasicParsing -TimeoutSec 2 | Out-Null
        return $true
    } catch { return $false }
}

# ── 1) ComfyUI の場所を探す ──────────────────────────────────────
$ComfyDir = $env:COMFYUI_DIR
if (-not $ComfyDir) {
    $candidates = @(
        "$env:USERPROFILE\ComfyUI",
        "$env:USERPROFILE\ComfyUI_windows_portable\ComfyUI",
        "$env:USERPROFILE\Documents\ComfyUI",
        "$env:USERPROFILE\Desktop\ComfyUI_windows_portable\ComfyUI",
        "C:\ComfyUI",
        "C:\ComfyUI_windows_portable\ComfyUI",
        "D:\ComfyUI",
        "D:\ComfyUI_windows_portable\ComfyUI"
    )
    foreach ($d in $candidates) {
        if (Test-Path "$d\main.py") { $ComfyDir = $d; break }
    }
}
if (-not $ComfyDir -or -not (Test-Path "$ComfyDir\main.py")) {
    Write-Error @"
ComfyUI が見つかりません。
  `$env:COMFYUI_DIR = "C:\path\to\ComfyUI" を設定してから再実行してください。
  未インストールなら docs/minimax-h3.md の手順を参照してください。
"@
    exit 1
}
Write-Host "▶ ComfyUI: $ComfyDir"

# ── ComfyUI 用 Python（ポータブル版 / venv / システム）───────────
$Portable = Join-Path (Split-Path $ComfyDir -Parent) "python_embeded\python.exe"
$ExtraPyArgs = @()
if (Test-Path $Portable) {
    $Python = $Portable
    $ExtraPyArgs = @("-s")           # ポータブル版の標準起動と同じ
} elseif (Test-Path "$ComfyDir\.venv\Scripts\python.exe") {
    $Python = "$ComfyDir\.venv\Scripts\python.exe"
} elseif (Test-Path "$ComfyDir\venv\Scripts\python.exe") {
    $Python = "$ComfyDir\venv\Scripts\python.exe"
} else {
    $Python = "python"
}

# ── -Update: ComfyUI 本体を更新（0.30.0 以降が MiniMax H3 対応）──
if ($Update) {
    Write-Host "▶ ComfyUI を更新中…"
    git -C $ComfyDir pull --ff-only
    & $Python -m pip install -q -r "$ComfyDir\requirements.txt"
    Write-Host "✓ 更新完了"
}

# ── 2) MiniMax H3 のモデルファイル確認 ───────────────────────────
$Models = Join-Path $ComfyDir "models"
$missing = @()
if (-not (Get-ChildItem "$Models\diffusion_models\minimax_h3_*.safetensors" -ErrorAction SilentlyContinue)) {
    $missing += "    models\diffusion_models\  … minimax_h3_*（拡散モデル）"
}
if (-not ((Get-ChildItem "$Models\text_encoders\*minimax_h3*.safetensors" -ErrorAction SilentlyContinue) +
          (Get-ChildItem "$Models\text_encoders\qwen3vl*.safetensors" -ErrorAction SilentlyContinue))) {
    $missing += "    models\text_encoders\     … qwen3vl_*_minimax_h3_*（テキストエンコーダ）"
}
if (-not (Get-ChildItem "$Models\vae\minimax_h3_*vae*.safetensors" -ErrorAction SilentlyContinue)) {
    $missing += "    models\vae\               … minimax_h3_video_vae / audio_vae"
}
if ($missing.Count -gt 0) {
    Write-Host "⚠️  MiniMax H3 のモデルファイルが見つかりません:" -ForegroundColor Yellow
    $missing | ForEach-Object { Write-Host $_ -ForegroundColor Yellow }
    Write-Host "   → ComfyUI のテンプレート（Template Library > Video > MiniMax H3）を開くと"
    Write-Host "     不足モデルのダウンロードを案内してくれます（手動配置は docs/minimax-h3.md 参照）。"
    Write-Host "   そのまま起動を続けます…"
}

# ── 3) すでに動いていればブラウザを開くだけ ──────────────────────
if (Test-ComfyAlive) {
    Write-Host "✓ ComfyUI はすでに起動しています → $Url"
    if (-not $NoBrowser) { Start-Process $Url }
    exit 0
}

# ポートを掴んだまま応答しない古いプロセスがいれば停止
Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }

# ── 4) 別ウィンドウで起動 → 準備完了を待つ → ブラウザ ────────────
Write-Host "▶ ComfyUI を起動します（ログは新しいウィンドウに表示されます）…"
$launchArgs = $ExtraPyArgs + @("main.py", "--listen", $Listen, "--port", $Port)
if (Test-Path $Portable) { $launchArgs += "--windows-standalone-build" }
if ($env:COMFYUI_ARGS) { $launchArgs += ($env:COMFYUI_ARGS -split " ") }
$proc = Start-Process -FilePath $Python -ArgumentList $launchArgs -WorkingDirectory $ComfyDir -PassThru

foreach ($i in 1..90) {
    if (Test-ComfyAlive) {
        Write-Host "✓ 起動完了 → $Url"
        if ($Lan) {
            Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
                Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' } |
                ForEach-Object { Write-Host "  他の端末から → http://$($_.IPAddress):$Port" }
            Write-Host "  ※ 初回は Windows ファイアウォールの許可ダイアログで「アクセスを許可」してください"
        }
        Write-Host "  ワークフロー: Template Library（テンプレート）> Video > MiniMax H3"
        if (-not $NoBrowser) { Start-Process $Url }
        exit 0
    }
    if ($proc.HasExited) {
        Write-Error "ComfyUI が起動に失敗しました。起動ウィンドウのエラーメッセージを確認してください。"
        exit 1
    }
    Start-Sleep -Seconds 1
}

Write-Error "90秒待っても応答がありません。ComfyUI のウィンドウを確認してください。"
exit 1

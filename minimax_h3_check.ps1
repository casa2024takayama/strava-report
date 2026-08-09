# ─────────────────────────────────────────────────────────────────
# minimax_h3_check.ps1 — Windows 環境を総点検してから MiniMax H3 を起動
#
#   git / ComfyUI / Python / モデルファイル / ポート / Tailscale / SSH を
#   [OK]/[NG] で一覧表示し、致命的な問題がなければそのまま起動する。
#
# 使い方:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\minimax_h3_check.ps1
#   （-NoLaunch を付けるとチェックのみで起動しない）
# ─────────────────────────────────────────────────────────────────
param([switch]$NoLaunch)

$ErrorActionPreference = "SilentlyContinue"
$script:fatal = $false

function OK($msg)   { Write-Host "  [OK] $msg" -ForegroundColor Green }
function NG($msg)   { Write-Host "  [NG] $msg" -ForegroundColor Red }
function WARN($msg) { Write-Host "  [--] $msg" -ForegroundColor Yellow }
function INFO($msg) { Write-Host "       $msg" -ForegroundColor Gray }

Write-Host "═══ MiniMax H3 環境チェック ═══"

# ── 1) PowerShell / OS ──────────────────────────────────────────
Write-Host "▼ 基本環境"
OK ("PowerShell {0} / {1}" -f $PSVersionTable.PSVersion, [Environment]::OSVersion.VersionString)

# ── 2) git とリポジトリの状態 ───────────────────────────────────
Write-Host "▼ リポジトリ"
if (Get-Command git) {
    $branch = git -C $PSScriptRoot rev-parse --abbrev-ref HEAD 2>$null
    $head   = git -C $PSScriptRoot log --oneline -1 2>$null
    if ($branch) { OK "ブランチ: $branch（$head）" } else { WARN "git リポジトリ情報を取得できません" }
} else {
    WARN "git が見つかりません（起動には不要。更新時のみ必要）"
}

# ── 3) ComfyUI 本体 ─────────────────────────────────────────────
Write-Host "▼ ComfyUI"
$ComfyDir = $env:COMFYUI_DIR
if (-not $ComfyDir) {
    $candidates = @(
        "$env:USERPROFILE\ComfyUI",
        "$env:USERPROFILE\ComfyUI_windows_portable\ComfyUI",
        "$env:USERPROFILE\Documents\ComfyUI",
        "$env:USERPROFILE\Desktop\ComfyUI_windows_portable\ComfyUI",
        "C:\ComfyUI", "C:\ComfyUI_windows_portable\ComfyUI",
        "D:\ComfyUI", "D:\ComfyUI_windows_portable\ComfyUI"
    )
    foreach ($d in $candidates) { if (Test-Path "$d\main.py") { $ComfyDir = $d; break } }
}
if ($ComfyDir -and (Test-Path "$ComfyDir\main.py")) {
    OK "本体: $ComfyDir"
    $verFile = Join-Path $ComfyDir "comfyui_version.py"
    $ver = $null
    if (Test-Path $verFile) {
        $ver = (Select-String -Path $verFile -Pattern '__version__\s*=\s*"([^"]+)"').Matches |
               ForEach-Object { $_.Groups[1].Value } | Select-Object -First 1
    }
    if ($ver) {
        if ([version]($ver -replace '[^\d.].*$','') -ge [version]"0.30.0") {
            OK "バージョン: $ver（MiniMax H3 対応）"
        } else {
            NG "バージョン: $ver — MiniMax H3 には 0.30.0 以降が必要"
            INFO "→ .\minimax_h3.ps1 -Update で更新できます"
        }
    } else {
        WARN "バージョンを判定できません（古い場合は .\minimax_h3.ps1 -Update）"
    }
} else {
    NG "ComfyUI が見つかりません"
    INFO "→ インストール済みなら: setx COMFYUI_DIR `"C:\path\to\ComfyUI`" 後、新しいウィンドウで再実行"
    INFO "→ 未インストールなら docs/minimax-h3.md を参照"
    $script:fatal = $true
}

# ── 4) Python ───────────────────────────────────────────────────
Write-Host "▼ Python"
$Python = $null
if ($ComfyDir) {
    $portable = Join-Path (Split-Path $ComfyDir -Parent) "python_embeded\python.exe"
    if (Test-Path $portable) { $Python = $portable; $kind = "ポータブル版同梱" }
    elseif (Test-Path "$ComfyDir\.venv\Scripts\python.exe") { $Python = "$ComfyDir\.venv\Scripts\python.exe"; $kind = "venv" }
    elseif (Test-Path "$ComfyDir\venv\Scripts\python.exe")  { $Python = "$ComfyDir\venv\Scripts\python.exe";  $kind = "venv" }
    elseif (Get-Command python) { $Python = "python"; $kind = "システム" }
}
if ($Python) {
    $pv = & $Python --version 2>&1
    OK "$pv（$kind: $Python）"
    $torch = & $Python -c "import torch; print(torch.__version__, 'cuda' if torch.cuda.is_available() else 'cpu')" 2>$null
    if ($torch) {
        if ($torch -match "cuda") { OK "PyTorch: $torch" }
        else { WARN "PyTorch: $torch — GPU(CUDA) が使えていません。生成が非常に遅くなります" }
    } else {
        WARN "PyTorch を確認できません（ComfyUI が起動できるなら問題ありません）"
    }
} elseif (-not $script:fatal) {
    NG "Python が見つかりません"
    $script:fatal = $true
}

# ── 5) GPU ──────────────────────────────────────────────────────
Write-Host "▼ GPU"
$gpu = Get-CimInstance Win32_VideoController | Select-Object -First 1
if ($gpu) {
    $vramGB = [math]::Round($gpu.AdapterRAM / 1GB, 1)
    if ($vramGB -gt 0) { OK "$($gpu.Name)（VRAM 約 ${vramGB}GB ※4GB超は不正確な場合あり）" }
    else { OK "$($gpu.Name)" }
    if ($gpu.Name -notmatch "NVIDIA|Radeon|Arc") { WARN "専用GPUではない可能性 — MiniMax H3 の生成は現実的でないかもしれません" }
} else {
    WARN "GPU 情報を取得できません"
}

# ── 6) MiniMax H3 モデルファイル ────────────────────────────────
Write-Host "▼ MiniMax H3 モデル"
if ($ComfyDir) {
    $Models = Join-Path $ComfyDir "models"
    $checks = @(
        @{ label = "拡散モデル (diffusion_models\minimax_h3_*)"; pat = "$Models\diffusion_models\minimax_h3_*.safetensors" },
        @{ label = "テキストエンコーダ (text_encoders\qwen3vl*/minimax_h3*)"; pat = @("$Models\text_encoders\*minimax_h3*.safetensors", "$Models\text_encoders\qwen3vl*.safetensors") },
        @{ label = "VAE (vae\minimax_h3_*vae*)"; pat = "$Models\vae\minimax_h3_*vae*.safetensors" }
    )
    foreach ($c in $checks) {
        $found = $c.pat | ForEach-Object { Get-ChildItem $_ } | Select-Object -First 1
        if ($found) { OK "$($c.label): $($found.Name)" }
        else { WARN "$($c.label): 未配置" }
    }
    INFO "未配置でも起動可。起動後に Template Library > Video > MiniMax H3 を開くとダウンロード案内が出ます"
}

# ── 7) ポート / 起動状態 ────────────────────────────────────────
Write-Host "▼ サーバー状態"
$Port = if ($env:COMFYUI_PORT) { $env:COMFYUI_PORT } else { "8188" }
$alive = $false
try { Invoke-WebRequest -Uri "http://127.0.0.1:$Port/system_stats" -UseBasicParsing -TimeoutSec 2 | Out-Null; $alive = $true } catch {}
if ($alive) {
    OK "ComfyUI は既にポート $Port で稼働中"
} else {
    $holder = Get-NetTCPConnection -LocalPort $Port -State Listen
    if ($holder) { WARN "ポート $Port を別プロセスが使用中（起動時に自動停止します）" }
    else { OK "ポート $Port は空いています" }
}

# ── 8) Mac からのリモート起動の前提（任意）─────────────────────
Write-Host "▼ リモート起動（Mac から使う場合のみ）"
if (Get-Command tailscale) {
    $tsip = tailscale ip -4 2>$null | Select-Object -First 1
    if ($tsip) { OK "Tailscale: $tsip（Mac の .env に MINIMAX_H3_HOST=$tsip）" }
    else { WARN "Tailscale はあるが未接続" }
} else {
    WARN "Tailscale 未インストール（LAN の IP でも可）"
}
$sshd = Get-Service sshd
if ($sshd) {
    if ($sshd.Status -eq "Running") { OK "OpenSSH サーバー: 稼働中" }
    else { WARN "OpenSSH サーバー: 停止中（Mac からの自動起動に必要。Start-Service sshd）" }
} else {
    WARN "OpenSSH サーバー: 未インストール（Mac からの自動起動に必要。docs/minimax-h3.md 参照）"
}

# ── まとめ & 起動 ───────────────────────────────────────────────
Write-Host ""
if ($script:fatal) {
    Write-Host "✗ 致命的な問題があるため起動しません。上の [NG] を解消してください。" -ForegroundColor Red
    exit 1
}
if ($NoLaunch) {
    Write-Host "✓ チェック完了（-NoLaunch のため起動はしません）" -ForegroundColor Green
    exit 0
}
Write-Host "✓ チェック完了 — MiniMax H3 を起動します…" -ForegroundColor Green
Write-Host ""
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "minimax_h3.ps1")

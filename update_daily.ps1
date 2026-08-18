# Ежедневный лёгкий прогон (~6 минут вместе с публикацией)
# Обновляет: цены и капитализацию (Binance/CoinGecko), TVL/комиссии/стейблкоины/DEX
# (DefiLlama), число валидаторов (RPC сетей) — копит историю для node_growth, ранг Coinbase,
# и ЛЁГКУЮ часть Nansen: перп-скринер + flow-intelligence, 8 кредитов. Именно она копит
# точки для факторов лесенки fresh_wallets_flow, exchange_flow, sm_perp_skew.
# Тяжёлую часть Nansen (киты + спот, +19 кредитов) коллектор сам включает раз в неделю.
#
# Запускается задачей Планировщика по расписанию И при пробуждении компьютера,
# поэтому есть защита: ровно один прогон в календарный день. Игнорировать — флаг -Force.
# Публикация сайта включена: после экспорта данные уезжают на
# https://cryptoandy1.github.io/EpicFundamental/ (force-push в ветку gh-pages).
# Собрать без публикации: .\update_daily.ps1 -NoDeploy
param([switch]$NoDeploy, [switch]$Force)
$ErrorActionPreference = "Continue"  # WARNING'и python/npm в stderr — не повод падать
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $root "backend\logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "daily.log"
$stamp = Join-Path $logDir "daily.stamp"
$py = Join-Path $root "backend\.venv\Scripts\python.exe"

function Write-Log($Text) { $Text | Out-File -Append -Encoding utf8 $log }

function Invoke-Step($Title, $Arguments) {
    Write-Log "--- $(Get-Date -Format 'HH:mm:ss') $Title"
    & $py @Arguments 2>&1 | Out-File -Append -Encoding utf8 $log
    if ($LASTEXITCODE -ne 0) {
        Write-Log "ОШИБКА ($Title): код $LASTEXITCODE"
        return $false
    }
    return $true
}

# --- ровно один прогон в календарный день ---
# Задача срабатывает и при пробуждении компьютера, и в 18:00: первый сработавший
# триггер делает прогон, остальные в этот день пропускаются.
if (-not $Force -and (Test-Path $stamp)) {
    # Out-File/Set-Content в PS 5.1 умеют дописывать BOM — снимаем, иначе TryParse молча падает
    $last = ((Get-Content $stamp -TotalCount 1) -replace "^\uFEFF", "").Trim()
    $lastRun = [datetime]::MinValue
    if ([datetime]::TryParse($last, [ref]$lastRun) -and $lastRun.Date -eq (Get-Date).Date) {
        Write-Log "=== $(Get-Date -Format 'yyyy-MM-dd HH:mm') пропуск: сегодня уже собирали ($last)"
        return
    }
}

Write-Log "=== $(Get-Date -Format 'yyyy-MM-dd HH:mm') ежедневный прогон"
Push-Location "$root\backend"
try {
    $ok = Invoke-Step "сбор данных" @(
        "-m", "app", "update",
        "--collector", "market", "--collector", "btc", "--collector", "nodes",
        "--collector", "defillama", "--collector", "coinbase_app", "--collector", "nansen"
    )
    if ($ok) { $ok = Invoke-Step "экспорт JSON" @("-m", "app", "export") }
} finally {
    Pop-Location
}

if ($ok -and -not $NoDeploy) {
    Write-Log "--- $(Get-Date -Format 'HH:mm:ss') публикация на GitHub Pages"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$root\deploy.ps1" 2>&1 |
        Out-File -Append -Encoding utf8 $log
    if ($LASTEXITCODE -ne 0) { Write-Log "ОШИБКА публикации: код $LASTEXITCODE" }
}
if ($ok) { [System.IO.File]::WriteAllText($stamp, (Get-Date).ToString("o")) }  # без BOM
Write-Log "=== $(Get-Date -Format 'yyyy-MM-dd HH:mm') готово"

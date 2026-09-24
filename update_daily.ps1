# Ежедневный лёгкий прогон (~6 минут вместе с публикацией)
# Обновляет: цены и капитализацию (Binance/CoinGecko), TVL/комиссии/стейблкоины/DEX
# (DefiLlama), число валидаторов (RPC сетей) — копит историю для node_growth, ранг Coinbase,
# ворота входа в лесенку (доминация BTC + доля альтов, обгоняющих BTC) и ЛЁГКУЮ часть
# Nansen: перп-скринер + flow-intelligence, 8 кредитов. Именно она копит точки для факторов
# лесенки fresh_wallets_flow, exchange_flow, sm_perp_skew.
# Тяжёлую часть Nansen (киты + спот, +19 кредитов) коллектор сам включает раз в неделю.
#
# Запускается задачей Планировщика по расписанию И при пробуждении компьютера,
# поэтому есть защита: ровно один прогон в календарный день. Игнорировать — флаг -Force.
# Публикация сайта включена: после экспорта данные уезжают на
# https://cryptoandy1.github.io/EpicFundamental/ (force-push в ветку gh-pages).
# Собрать без публикации: .\update_daily.ps1 -NoDeploy
#
# При сбое: backend\logs\LAST_FAILURE.txt + алерт + КОД ВОЗВРАТА 1 (Планировщик покажет
# LastTaskResult ≠ 0). Раньше скрипт выходил с 0, и сбой 19.08–22.09.2026 не был замечен.
param([switch]$NoDeploy, [switch]$Force)
$ErrorActionPreference = "Continue"  # WARNING'и python/npm в stderr — не повод падать
$env:PYTHONUTF8 = "1"                # stdout в пайп Планировщика: иначе cp1252 и UnicodeEncodeError
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:LogDir = Join-Path $root "backend\logs"
New-Item -ItemType Directory -Force $script:LogDir | Out-Null
$script:LogPath = Join-Path $script:LogDir "daily.log"
$script:Py = Join-Path $root "backend\.venv\Scripts\python.exe"
$script:RunName = "daily"
$stamp = Join-Path $script:LogDir "daily.stamp"
. "$root\update_common.ps1"

# --- ровно один прогон в календарный день ---
# Задача срабатывает и при пробуждении компьютера, и в 18:00: первый сработавший
# триггер делает прогон, остальные в этот день пропускаются.
if (-not $Force -and (Test-Path $stamp)) {
    # Out-File/Set-Content в PS 5.1 умеют дописывать BOM — снимаем, иначе TryParse молча падает
    $last = ([string](Get-Content $stamp -TotalCount 1 -ErrorAction SilentlyContinue)).TrimStart([char]0xFEFF).Trim()
    $lastRun = [datetime]::MinValue
    if ([datetime]::TryParse($last, [ref]$lastRun) -and $lastRun.Date -eq (Get-Date).Date) {
        Write-Log "=== $(Get-Date -Format 'yyyy-MM-dd HH:mm') пропуск: сегодня уже собирали ($last)"
        return
    }
}

if (-not (Enter-UpdateLock)) { return }   # недельный прогон уже работает с этой же БД

Write-Log "=== $(Get-Date -Format 'yyyy-MM-dd HH:mm') ежедневный прогон"
$ok = $false
Push-Location "$root\backend"
try {
    $ok = Invoke-Step "сбор данных" @(
        "-m", "app", "update",
        "--collector", "market", "--collector", "btc", "--collector", "nodes",
        "--collector", "defillama", "--collector", "coinbase_app", "--collector", "altseason",
        "--collector", "nansen"
    )
    if ($ok) { $ok = Invoke-Step "экспорт JSON" @("-m", "app", "export") }
    # оповещения о СИГНАЛАХ (не о сбоях): нефатально, код возврата не проверяем
    if ($ok) { Invoke-Step "сигналы (Telegram)" @("-m", "app", "notify") | Out-Null }
} finally {
    Pop-Location
}

$deployOk = $true
if ($ok -and -not $NoDeploy) {
    Write-Log "--- $(Get-Date -Format 'HH:mm:ss') публикация на GitHub Pages"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$root\deploy.ps1" 2>&1 |
        Out-File -Append -Encoding utf8 $script:LogPath
    if ($LASTEXITCODE -ne 0) {
        $deployOk = $false
        Write-Log "ОШИБКА публикации: код $LASTEXITCODE"
    }
}

$code = Complete-Run $ok $deployOk $stamp
Exit-UpdateLock
exit $code

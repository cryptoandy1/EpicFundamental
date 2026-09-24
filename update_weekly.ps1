# Недельный полный прогон (~25 минут вместе с публикацией)
# Всё из ежедневного плюс медленные коллекторы: GitHub (активные разработчики ядра
# и новые репо экосистемы), Google Trends, СМИ (GDELT), разлоки, кошельки команды, Discord,
# и ПОЛНАЯ часть Nansen — позиции китов и спотовая когорта Smart Money. Плюс скринер
# кандидатов (ф.3) — иначе список кандидатов обновляется только руками.
#
# Запускается задачей Планировщика по расписанию И при пробуждении компьютера,
# поэтому есть защита: один прогон в неделю, по воскресеньям. Игнорировать — флаг -Force.
# Публикация сайта включена: после экспорта данные уезжают на
# https://cryptoandy1.github.io/EpicFundamental/ (force-push в ветку gh-pages).
# Собрать без публикации: .\update_weekly.ps1 -NoDeploy
#
# При сбое: backend\logs\LAST_FAILURE.txt + алерт + КОД ВОЗВРАТА 1 (см. update_daily.ps1).
param([switch]$NoDeploy, [switch]$Force)
$ErrorActionPreference = "Continue"  # WARNING'и python/npm в stderr — не повод падать
$env:PYTHONUTF8 = "1"                # stdout в пайп Планировщика: иначе cp1252 и UnicodeEncodeError
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:LogDir = Join-Path $root "backend\logs"
New-Item -ItemType Directory -Force $script:LogDir | Out-Null
$script:LogPath = Join-Path $script:LogDir "weekly.log"
$script:Py = Join-Path $root "backend\.venv\Scripts\python.exe"
$script:RunName = "weekly"
$stamp = Join-Path $script:LogDir "weekly.stamp"
$dailyStamp = Join-Path $script:LogDir "daily.stamp"
. "$root\update_common.ps1"

# --- раз в неделю, в воскресенье ---
# Задача срабатывает при каждом пробуждении: в воскресенье прогон идёт сразу после
# пробуждения, в остальные дни пропускается. Если воскресенье пропущено целиком
# (компьютер не включали) — догоняем на 8-й день.
if (-not $Force -and (Test-Path $stamp)) {
    # Out-File/Set-Content в PS 5.1 умеют дописывать BOM — снимаем, иначе TryParse молча падает
    $last = ([string](Get-Content $stamp -TotalCount 1 -ErrorAction SilentlyContinue)).TrimStart([char]0xFEFF).Trim()
    $lastRun = [datetime]::MinValue
    if ([datetime]::TryParse($last, [ref]$lastRun)) {
        $days = ((Get-Date).Date - $lastRun.Date).TotalDays
        $isSunday = (Get-Date).DayOfWeek -eq [DayOfWeek]::Sunday
        if ($days -lt 6) {
            Write-Log "=== $(Get-Date -Format 'yyyy-MM-dd HH:mm') пропуск: полный прогон был $days дн. назад ($last)"
            return
        }
        if (-not $isSunday -and $days -lt 8) {
            Write-Log "=== $(Get-Date -Format 'yyyy-MM-dd HH:mm') пропуск: ждём воскресенья (прошло $days дн.)"
            return
        }
    }
}

if (-not (Enter-UpdateLock)) { return }   # дневной прогон уже работает с этой же БД

Write-Log "=== $(Get-Date -Format 'yyyy-MM-dd HH:mm') недельный прогон"
$ok = $false
Push-Location "$root\backend"
try {
    $ok = Invoke-Step "полный сбор данных" @("-m", "app", "update")
    # скринер не влияет на успех прогона: CoinGecko капризен, а данные справочные
    if ($ok) { Invoke-Step "скринер кандидатов (ф.3)" @("-m", "app", "screen") | Out-Null }
    # бэктест лесенки: as-of ранги против последующей доходности; нефатально
    if ($ok) { Invoke-Step "бэктест лесенки" @("-m", "app", "backtest", "--start", "2024-01-01") | Out-Null }
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

# недельный прогон — надмножество дневного: отмечаем и дневной штамп, иначе вечером
# того же воскресенья дневная задача пошла бы собирать всё заново
if ($ok) { [System.IO.File]::WriteAllText($dailyStamp, (Get-Date).ToString("o")) }

$code = Complete-Run $ok $deployOk $stamp
Exit-UpdateLock
exit $code

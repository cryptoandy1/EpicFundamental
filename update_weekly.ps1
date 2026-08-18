# Недельный полный прогон (~25 минут вместе с публикацией)
# Всё из ежедневного плюс медленные коллекторы: GitHub (активные разработчики ядра
# и новые репо экосистемы), Google Trends, СМИ (GDELT), разлоки, кошельки команды, Discord,
# и ПОЛНАЯ часть Nansen — позиции китов и спотовая когорта Smart Money.
#
# Запускается задачей Планировщика по расписанию И при пробуждении компьютера,
# поэтому есть защита: один прогон в неделю, по воскресеньям. Игнорировать — флаг -Force.
# Публикация сайта включена: после экспорта данные уезжают на
# https://cryptoandy1.github.io/EpicFundamental/ (force-push в ветку gh-pages).
# Собрать без публикации: .\update_weekly.ps1 -NoDeploy
param([switch]$NoDeploy, [switch]$Force)
$ErrorActionPreference = "Continue"  # WARNING'и python/npm в stderr — не повод падать
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $root "backend\logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "weekly.log"
$stamp = Join-Path $logDir "weekly.stamp"
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

# --- раз в неделю, в воскресенье ---
# Задача срабатывает при каждом пробуждении: в воскресенье прогон идёт сразу после
# пробуждения, в остальные дни пропускается. Если воскресенье пропущено целиком
# (компьютер не включали) — догоняем на 8-й день.
if (-not $Force -and (Test-Path $stamp)) {
    # Out-File/Set-Content в PS 5.1 умеют дописывать BOM — снимаем, иначе TryParse молча падает
    $last = ((Get-Content $stamp -TotalCount 1) -replace "^\uFEFF", "").Trim()
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

Write-Log "=== $(Get-Date -Format 'yyyy-MM-dd HH:mm') недельный прогон"
Push-Location "$root\backend"
try {
    $ok = Invoke-Step "полный сбор данных" @("-m", "app", "update")
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

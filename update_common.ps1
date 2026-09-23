# Общая обвязка для update_daily.ps1 / update_weekly.ps1 (dot-source: . "$root\update_common.ps1").
# Вызывающий скрипт заранее задаёт $script:LogPath, $script:LogDir, $script:Py, $script:RunName.
#
# Windows PowerShell 5.1: файл обязан быть в UTF-8 С BOM, иначе кириллица читается как ANSI
# и ломает парсер. $ErrorActionPreference = "Continue" + проверка $LASTEXITCODE: python пишет
# в stderr обычные WARNING'и ретраев, при "Stop" каждая такая строка рвала бы скрипт.

function Write-Log($Text) { $Text | Out-File -Append -Encoding utf8 $script:LogPath }

function Invoke-Step($Title, $Arguments) {
    Write-Log "--- $(Get-Date -Format 'HH:mm:ss') $Title"
    & $script:Py @Arguments 2>&1 | Out-File -Append -Encoding utf8 $script:LogPath
    if ($LASTEXITCODE -ne 0) {
        Write-Log "ОШИБКА ($Title): код $LASTEXITCODE"
        return $false
    }
    return $true
}

# --- Алерт о сбое -------------------------------------------------------------
# Сбой 19.08–22.09.2026 остался незамеченным 35 дней: скрипты выходили с кодом 0,
# Планировщик рапортовал успех. Теперь при сбое: файл-маркер + Telegram (если есть
# ключи в backend/.env) или окно msg.exe, и ненулевой код возврата.
function Send-Alert($Text) {
    $envFile = Join-Path (Split-Path -Parent $script:LogDir) ".env"
    $token = ""
    $chat = ""
    if (Test-Path $envFile) {
        foreach ($line in (Get-Content $envFile -ErrorAction SilentlyContinue)) {
            if ($line -match '^\s*TELEGRAM_BOT_TOKEN\s*=\s*(.+)$') { $token = $Matches[1].Trim() }
            if ($line -match '^\s*TELEGRAM_CHAT_ID\s*=\s*(.+)$') { $chat = $Matches[1].Trim() }
        }
    }
    if ($token -and $chat) {
        try {
            $uri = "https://api.telegram.org/bot$token/sendMessage"
            Invoke-RestMethod -Method Post -Uri $uri -Body @{ chat_id = $chat; text = $Text } | Out-Null
            Write-Log "алерт отправлен в Telegram"
            return
        } catch {
            Write-Log "Telegram недоступен: $($_.Exception.Message)"
        }
    }
    try { & msg.exe $env:USERNAME /TIME:600 $Text 2>$null | Out-Null } catch { }
}

# --- Взаимное исключение дневного и недельного прогонов ------------------------
# 21.09.2026 в 22:21 оба стартовали одновременно на одной SQLite-базе. Замок считается
# протухшим, если процесс мёртв или файлу больше 3 ч (= ExecutionTimeLimit задачи).
function Enter-UpdateLock {
    $lock = Join-Path $script:LogDir "update.lock"
    if (Test-Path $lock) {
        $raw = ([string](Get-Content $lock -TotalCount 1 -ErrorAction SilentlyContinue)).TrimStart([char]0xFEFF).Trim()
        $parts = $raw -split '\s+', 2
        $holder = 0
        [void][int]::TryParse($parts[0], [ref]$holder)
        $started = [datetime]::MinValue
        $ageHours = 99.0
        if ($parts.Count -gt 1 -and [datetime]::TryParse($parts[1], [ref]$started)) {
            $ageHours = ((Get-Date) - $started).TotalHours
        }
        $alive = $false
        if ($holder -gt 0 -and $holder -ne $PID) {
            $alive = $null -ne (Get-Process -Id $holder -ErrorAction SilentlyContinue)
        }
        if ($alive -and $ageHours -lt 3) {
            Write-Log "=== $(Get-Date -Format 'yyyy-MM-dd HH:mm') пропуск: идёт другой прогон (PID $holder)"
            return $false
        }
        Write-Log "замок протух (PID $holder, возраст $([math]::Round($ageHours, 1)) ч) — перехватываем"
    }
    [System.IO.File]::WriteAllText($lock, "$PID $((Get-Date).ToString('o'))")
    return $true
}

function Exit-UpdateLock {
    $lock = Join-Path $script:LogDir "update.lock"
    if (-not (Test-Path $lock)) { return }
    $raw = ([string](Get-Content $lock -TotalCount 1 -ErrorAction SilentlyContinue)).TrimStart([char]0xFEFF).Trim()
    if ($raw -like "$PID *") { Remove-Item $lock -Force -ErrorAction SilentlyContinue }
}

# --- Общий финал прогона -------------------------------------------------------
# Штамп ставим, если ДАННЫЕ собраны (даже когда упала публикация): иначе следующий
# триггер в тот же день собирал бы всё заново.
function Complete-Run($CollectOk, $DeployOk, $StampPath) {
    $failFile = Join-Path $script:LogDir "LAST_FAILURE.txt"
    if ($CollectOk) { [System.IO.File]::WriteAllText($StampPath, (Get-Date).ToString("o")) }  # без BOM
    if ($CollectOk -and $DeployOk) {
        Remove-Item $failFile -Force -ErrorAction SilentlyContinue
        Write-Log "=== $(Get-Date -Format 'yyyy-MM-dd HH:mm') готово"
        return 0
    }
    $what = if (-not $CollectOk) { "сбор данных" } else { "публикация" }
    $msg = "EpicFundamental $($script:RunName): сбой ($what), $(Get-Date -Format 'yyyy-MM-dd HH:mm'). См. backend\logs\$($script:RunName).log"
    [System.IO.File]::WriteAllText($failFile, $msg)
    Send-Alert $msg
    Write-Log "=== $msg"
    return 1
}

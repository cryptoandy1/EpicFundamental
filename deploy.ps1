# Публикация дашборда на GitHub Pages (ветка gh-pages).
# Запуск из корня репозитория: .\deploy.ps1
# Шаги: свежие JSON-снапшоты из локальной БД -> статическая сборка Next.js ->
# принудительный пуш содержимого frontend/out в ветку gh-pages.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = "https://github.com/cryptoandy1/EpicFundamental.git"

Push-Location "$root\backend"
& .\.venv\Scripts\python.exe -m app export
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "export: ошибка" }
Pop-Location

Push-Location "$root\frontend"
$env:STATIC_EXPORT = "1"
npm run build
$build_ok = $LASTEXITCODE
Remove-Item Env:\STATIC_EXPORT
if ($build_ok -ne 0) { Pop-Location; throw "next build: ошибка" }
Pop-Location

Push-Location "$root\frontend\out"
try {
    git init -q -b gh-pages
    git add -A
    git commit -q -m "deploy $(Get-Date -Format yyyy-MM-dd_HHmm)"
    git push -f $repo gh-pages:gh-pages
} finally {
    Remove-Item -Recurse -Force "$root\frontend\out\.git"
    Pop-Location
}
Write-Host "Готово: https://cryptoandy1.github.io/EpicFundamental/"

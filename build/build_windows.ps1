# Build BackgroundAssistant for Windows (Phase 7).
#
#   powershell -ExecutionPolicy Bypass -File build\build_windows.ps1
#
# Produces dist\BackgroundAssistant\ and, if Inno Setup is installed, a
# per-user installer that needs no administrator rights.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "==> Icons"
python tools\make_icons.py --out assets

Write-Host "==> Tests"
python -m pytest -q

Write-Host "==> Headless check"
python main.py --check

Write-Host "==> PyInstaller"
if (Test-Path dist) { Remove-Item -Recurse -Force dist }
pyinstaller --clean --noconfirm --workpath build\work --distpath dist `
    build\backgroundassistant.spec

Write-Host "==> Smoke test"
& "dist\BackgroundAssistant\BackgroundAssistant.exe" --check

$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (Test-Path $iscc) {
    Write-Host "==> Installer"
    & $iscc build\installer.iss
} else {
    Write-Host "Inno Setup not found; skipping the installer."
}

Write-Host ""
Write-Host "Built: dist\BackgroundAssistant\"
Write-Host "Note: an unsigned build triggers a SmartScreen warning on first run."

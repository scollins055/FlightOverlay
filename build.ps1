# Builds dist\FlightOverlay\FlightOverlay.exe (a folder you can copy anywhere; no Python needed).
#   powershell -ExecutionPolicy Bypass -File build.ps1            # build
#   powershell -ExecutionPolicy Bypass -File build.ps1 -Shortcut  # build and add a desktop shortcut
param([switch]$Shortcut)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (-not (Test-Path .venv\Scripts\python.exe)) {
    Write-Host 'Creating virtual environment...'
    py -3 -m venv .venv
}
$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }

& powershell -ExecutionPolicy Bypass -File tools\get_ffmpeg.ps1
if ($LASTEXITCODE -ne 0) { throw 'FFmpeg download failed' }

& $py tools\make_icon.py
& $py -m PyInstaller --noconfirm --clean FlightOverlay.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }

$exe = Join-Path $PSScriptRoot 'dist\FlightOverlay\FlightOverlay.exe'
Write-Host "Built $exe"

if ($Shortcut) {
    $lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'FlightOverlay.lnk'
    $ws = New-Object -ComObject WScript.Shell
    $s = $ws.CreateShortcut($lnk)
    $s.TargetPath = $exe
    $s.WorkingDirectory = Split-Path $exe
    $s.IconLocation = $exe
    $s.Save()
    Write-Host "Desktop shortcut created: $lnk"
}

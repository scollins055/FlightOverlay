# Downloads a pinned FFmpeg build (ffmpeg.exe + ffprobe.exe) into vendor\ffmpeg\.
# Run from the project root:  powershell -ExecutionPolicy Bypass -File tools\get_ffmpeg.ps1
$ErrorActionPreference = 'Stop'
$Version = '7.1.1'
$Url = "https://github.com/GyanD/codexffmpeg/releases/download/$Version/ffmpeg-$Version-essentials_build.zip"

$Root = Split-Path -Parent $PSScriptRoot
$Dest = Join-Path $Root 'vendor\ffmpeg'
if ((Test-Path (Join-Path $Dest 'ffmpeg.exe')) -and (Test-Path (Join-Path $Dest 'ffprobe.exe'))) {
    Write-Host "FFmpeg already present in $Dest"
    exit 0
}

$Tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("ffmpeg_dl_" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force $Tmp | Out-Null
try {
    $Zip = Join-Path $Tmp 'ffmpeg.zip'
    Write-Host "Downloading FFmpeg $Version ..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $Url -OutFile $Zip -UseBasicParsing
    Expand-Archive -Path $Zip -DestinationPath $Tmp -Force
    $Bin = Get-ChildItem -Path $Tmp -Recurse -Filter 'ffmpeg.exe' | Select-Object -First 1
    if (-not $Bin) { throw 'ffmpeg.exe not found in archive' }
    New-Item -ItemType Directory -Force $Dest | Out-Null
    Copy-Item (Join-Path $Bin.DirectoryName 'ffmpeg.exe') $Dest -Force
    Copy-Item (Join-Path $Bin.DirectoryName 'ffprobe.exe') $Dest -Force
    $Lic = Get-ChildItem -Path $Tmp -Recurse -Filter 'LICENSE*' | Select-Object -First 1
    if ($Lic) { Copy-Item $Lic.FullName (Join-Path $Dest 'FFMPEG_LICENSE.txt') -Force }
    Write-Host "FFmpeg installed to $Dest"
}
finally {
    Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
}

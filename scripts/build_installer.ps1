$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

& $Python "scripts\prepare_ffmpeg.py"
& $Python -m PyInstaller --noconfirm "installer.spec"

$DiscoveredInno = @()
$LocalPrograms = Join-Path $env:LOCALAPPDATA "Programs"
if (Test-Path $LocalPrograms) {
    $DiscoveredInno = Get-ChildItem -Path $LocalPrograms -Filter "ISCC.exe" -Recurse -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty FullName
}

[array]$InnoCandidates = @(
    (Get-Command "iscc.exe" -ErrorAction SilentlyContinue).Source,
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    $DiscoveredInno
) | Where-Object { $_ -and (Test-Path $_) }

if ($InnoCandidates.Count -eq 0) {
    Write-Host ""
    Write-Host "PyInstaller app folder created: dist\BiliDownloader"
    Write-Host "Inno Setup was not found. Install Inno Setup 6, then run this script again."
    Write-Host "Installer script: installer\BiliDownloader.iss"
    exit 0
}

$InnoCompiler = [string]$InnoCandidates[0]
& $InnoCompiler "installer\BiliDownloader.iss"
Write-Host ""
Write-Host "Installer created: dist\installer\BiliDownloader-Setup.exe"

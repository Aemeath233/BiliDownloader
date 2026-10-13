$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$UvCommand = Get-Command "uv.exe" -ErrorAction SilentlyContinue
if ($UvCommand) {
    $PythonExecutable = $UvCommand.Source
    $PythonPrefix = @("run", "--no-project", "--python", "3.11", "--with-requirements", "requirements-build.txt", "python")
} else {
    $PythonExecutable = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $PythonExecutable)) {
        $PythonExecutable = (Get-Command "python.exe" -ErrorAction Stop).Source
    }
    $PythonPrefix = @()
}

function Invoke-ProjectPython {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & $PythonExecutable @PythonPrefix @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code $LASTEXITCODE"
    }
}

if (-not (Test-Path -LiteralPath "vendor\ffmpeg\bin\ffmpeg.exe") -or -not (Test-Path -LiteralPath "vendor\ffmpeg\LICENSE.txt")) {
    Invoke-ProjectPython "scripts\prepare_ffmpeg.py"
}
Invoke-ProjectPython "-m" "PyInstaller" "--noconfirm" "--clean" "installer.spec"

[array]$InnoCandidates = @(
    (Get-Command "iscc.exe" -ErrorAction SilentlyContinue).Source,
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
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
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed with exit code $LASTEXITCODE" }
Write-Host ""
Write-Host "Installer created: dist\installer\BiliDownloader-Setup.exe"

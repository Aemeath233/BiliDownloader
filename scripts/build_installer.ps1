param([string]$InnoCompiler)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PreviousRuntimeFlag = $env:BILI_BUNDLE_WEBVIEW2
$PreviousFFmpegFlag = $env:BILI_FULL_FFMPEG
Push-Location -LiteralPath $ProjectRoot
try {
    $env:BILI_BUNDLE_WEBVIEW2 = "0"
    $env:BILI_FULL_FFMPEG = "0"
    if (-not $InnoCompiler) {
        [array]$InnoCandidates = @(
            (Get-Command "iscc.exe" -ErrorAction SilentlyContinue).Source,
            "$ProjectRoot/build/tools/inno/ISCC.exe",
            "$env:LOCALAPPDATA/Programs/Inno Setup 6/ISCC.exe",
            "${env:ProgramFiles(x86)}/Inno Setup 6/ISCC.exe",
            "$env:ProgramFiles/Inno Setup 6/ISCC.exe"
        ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
        if ($InnoCandidates.Count -eq 0) { throw "Inno Setup 6 is required. Install it or pass -InnoCompiler with the ISCC.exe path." }
        $InnoCompiler = [string]$InnoCandidates[0]
    }
    if (-not (Test-Path -LiteralPath "vendor/ffmpeg-minimal/bin/ffmpeg.exe")) {
        throw "Prepare the small remux FFmpeg first; see docs/BUILDING.md."
    }
    $UvCommand = Get-Command "uv.exe" -ErrorAction SilentlyContinue
    if ($UvCommand) {
        $BuildPython = $UvCommand.Source
        $BuildPrefix = @("run", "--no-project", "--python", "3.11", "--with-requirements", "requirements-build.txt", "python")
    } else {
        $BuildPython = Join-Path $ProjectRoot ".venv/Scripts/python.exe"
        $BuildPrefix = @()
    }
    function Invoke-BuildPython {
        param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
        & $BuildPython @BuildPrefix @Arguments
        if ($LASTEXITCODE -ne 0) { throw "Build command failed with exit code $LASTEXITCODE" }
    }
    Invoke-BuildPython "scripts/prepare_webview_bootstrapper.py"
    Invoke-BuildPython "-m" "PyInstaller" "--noconfirm" "--distpath" "dist/installer/app" "--workpath" "build/installer/pyinstaller" "installer.spec"
    & $InnoCompiler "installer/BiliDownloader.iss"
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed with exit code $LASTEXITCODE" }
    $SetupFile = "dist/installer/BiliDownloader-Setup.exe"
    if ((Get-Item -LiteralPath $SetupFile).Length -gt 50MB) {
        throw "Installer exceeds the 50 MiB size budget. Inspect bundled dependencies before release."
    }
    Copy-Item -LiteralPath "LICENSE", "THIRD_PARTY_NOTICES.md" -Destination "dist/installer" -Force
    Copy-Item -LiteralPath "vendor/ffmpeg-minimal/LICENSE.txt" -Destination "dist/installer/FFmpeg-LICENSE.txt" -Force
    Copy-Item -LiteralPath "vendor/ffmpeg-minimal/BUILD-INFO.txt" -Destination "dist/installer/FFmpeg-build-info.txt" -Force
    Copy-Item -LiteralPath "vendor/ffmpeg-minimal/SOURCE.json" -Destination "dist/installer/FFmpeg-source-info.json" -Force
    Copy-Item -LiteralPath "vendor/ffmpeg-minimal/FFmpeg-source-f044c5e627.tar.gz" -Destination "dist/installer" -Force
    $Hash = (Get-FileHash -LiteralPath $SetupFile -Algorithm SHA256).Hash.ToLowerInvariant()
    "$Hash  BiliDownloader-Setup.exe" | Set-Content -LiteralPath "dist/installer/SHA256SUMS.txt" -Encoding ascii
    Write-Host "Installer: $ProjectRoot/$SetupFile"
} finally {
    $env:BILI_BUNDLE_WEBVIEW2 = $PreviousRuntimeFlag
    $env:BILI_FULL_FFMPEG = $PreviousFFmpegFlag
    Pop-Location
}

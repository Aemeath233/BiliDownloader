param([switch]$IncludeFolder, [switch]$IncludeWebView2, [switch]$FullFFmpeg)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PreviousRuntimeFlag = $env:BILI_BUNDLE_WEBVIEW2
$PreviousFFmpegFlag = $env:BILI_FULL_FFMPEG
Push-Location -LiteralPath $ProjectRoot
try {
    $env:BILI_BUNDLE_WEBVIEW2 = if ($IncludeWebView2) { "1" } else { "0" }
    $env:BILI_FULL_FFMPEG = if ($FullFFmpeg) { "1" } else { "0" }
    if ($IncludeWebView2 -and -not (Test-Path -LiteralPath "vendor/webview2/msedgewebview2.exe")) {
        throw "Prepare the Microsoft x64 Fixed Version WebView2 runtime in vendor/webview2 first. See docs/BUILDING.md."
    }
    $OutputRoot = if ($IncludeWebView2) { "dist/self-contained" } else { "dist/portable" }
    $WorkRoot = if ($IncludeWebView2) { "build/self-contained" } else { "build/portable" }
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
    if ($FullFFmpeg -and (-not (Test-Path -LiteralPath "vendor/ffmpeg/bin/ffmpeg.exe") -or -not (Test-Path -LiteralPath "vendor/ffmpeg/LICENSE.txt"))) {
        Invoke-BuildPython "scripts/prepare_ffmpeg.py"
    }
    if (-not $FullFFmpeg -and -not (Test-Path -LiteralPath "vendor/ffmpeg-minimal/bin/ffmpeg.exe")) {
        throw "Build the small remux-only FFmpeg first; see docs/BUILDING.md. A full FFmpeg build requires explicit -FullFFmpeg."
    }
    Invoke-BuildPython "-m" "PyInstaller" "--noconfirm" "--distpath" $OutputRoot "--workpath" $WorkRoot "portable.spec"
    $PortableExe = Join-Path $OutputRoot "BiliDownloader-Portable.exe"
    if (-not $IncludeWebView2 -and -not $FullFFmpeg -and (Get-Item -LiteralPath $PortableExe).Length -gt 50MB) {
        throw "Standard portable build exceeds the 50 MiB size budget. Inspect bundled dependencies before release."
    }
    Write-Host "Portable executable: $ProjectRoot/$OutputRoot/BiliDownloader-Portable.exe"
    if ($IncludeFolder) {
        Invoke-BuildPython "-m" "PyInstaller" "--noconfirm" "--distpath" "$OutputRoot/folder" "--workpath" "$WorkRoot/folder" "main.spec"
        Compress-Archive -LiteralPath "$OutputRoot/folder/BiliDownloader" -DestinationPath "$OutputRoot/BiliDownloader-Folder-Portable.zip" -Force
        Write-Host "Folder portable archive: $ProjectRoot/$OutputRoot/BiliDownloader-Folder-Portable.zip"
    }
    Copy-Item -LiteralPath "LICENSE", "THIRD_PARTY_NOTICES.md" -Destination $OutputRoot -Force
    $FFmpegRoot = if ($FullFFmpeg) { "vendor/ffmpeg" } else { "vendor/ffmpeg-minimal" }
    Copy-Item -LiteralPath "$FFmpegRoot/LICENSE.txt" -Destination "$OutputRoot/FFmpeg-LICENSE.txt" -Force
    if (-not $FullFFmpeg) {
        Copy-Item -LiteralPath "$FFmpegRoot/BUILD-INFO.txt" -Destination "$OutputRoot/FFmpeg-build-info.txt" -Force
        Copy-Item -LiteralPath "$FFmpegRoot/SOURCE.json" -Destination "$OutputRoot/FFmpeg-source-info.json" -Force
        Copy-Item -LiteralPath "$FFmpegRoot/FFmpeg-source-f044c5e627.tar.gz" -Destination $OutputRoot -Force
    }
    $ArtifactNames = @("BiliDownloader-Portable.exe")
    if ($IncludeFolder) { $ArtifactNames += "BiliDownloader-Folder-Portable.zip" }
    $HashLines = foreach ($ArtifactName in $ArtifactNames) {
        $Hash = (Get-FileHash -LiteralPath (Join-Path $OutputRoot $ArtifactName) -Algorithm SHA256).Hash.ToLowerInvariant()
        "$Hash  $ArtifactName"
    }
    $HashLines | Set-Content -LiteralPath "$OutputRoot/SHA256SUMS.txt" -Encoding ascii
} finally {
    $env:BILI_BUNDLE_WEBVIEW2 = $PreviousRuntimeFlag
    $env:BILI_FULL_FFMPEG = $PreviousFFmpegFlag
    Pop-Location
}

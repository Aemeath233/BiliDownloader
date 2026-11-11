#Requires -Version 7.0
param(
    [Parameter(Mandatory)][string]$Source,
    [string]$Bash = 'bash',
    [string]$Make = 'make',
    [string]$ToolchainDirectory,
    [ValidateRange(1, 128)][int]$Jobs = [Math]::Min([Environment]::ProcessorCount, 8)
)
$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$commit = 'f044c5e62710a151e001ae097fd3a07b88540766'
$sourceHash = '842f845c8e7e5d338b60c3eeaeeefc0dc2da15401a7d12fb8a52dd56af2181df'
$archive = (Resolve-Path -LiteralPath $Source).Path
if ((Get-FileHash -LiteralPath $archive).Hash -ne $sourceHash) { throw 'Source SHA256 differs from the pinned FFmpeg archive' }
$output = Join-Path $workspace 'vendor/ffmpeg-minimal'
if (Test-Path -LiteralPath $output) { throw "Move the existing build aside before rebuilding: $output" }
$bashPath = (Get-Command $Bash -ErrorAction Stop).Source
$makePath = (Get-Command $Make -ErrorAction Stop).Source
$work = Join-Path $workspace ('build/ffmpeg-minimal/' + [guid]::NewGuid().ToString('N'))
$sourceDirectory = Join-Path $work 'source'
$compileDirectory = Join-Path $work 'compile'
New-Item -ItemType Directory -Path $sourceDirectory, $compileDirectory -Force | Out-Null
& tar -xf $archive -C $sourceDirectory
if ($LASTEXITCODE -ne 0) { throw 'FFmpeg source extraction failed' }
$roots = @(Get-ChildItem -LiteralPath $sourceDirectory -Directory | Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'configure') })
if ($roots.Count -ne 1) { throw 'Expected a single FFmpeg source root' }
$sourceRoot = $roots[0].FullName
$options = @(
    '--target-os=mingw32', '--arch=x86_64', '--cc=gcc', '--ar=ar', '--ranlib=ranlib',
    '--disable-autodetect', '--disable-iconv', '--disable-everything', '--disable-network',
    '--disable-doc', '--disable-debug', '--disable-shared', '--enable-static', '--disable-x86asm',
    '--disable-ffplay', '--disable-ffprobe', '--enable-ffmpeg', '--enable-small',
    '--disable-pthreads', '--enable-w32threads', '--extra-ldflags=-static', '--enable-protocol=file',
    '--enable-demuxer=mov,flv,matroska,aac,flac,ac3,eac3', '--enable-muxer=mp4',
    '--enable-decoder=aac,ac3,eac3,flac', '--enable-parser=h264,hevc,av1,aac,ac3,flac,mpegaudio',
    '--enable-bsf=aac_adtstoasc,extract_extradata'
)
$previousPath = $env:PATH
Push-Location $compileDirectory
try {
    if ($ToolchainDirectory) { $env:PATH = (Resolve-Path -LiteralPath $ToolchainDirectory).Path + [IO.Path]::PathSeparator + $previousPath }
    & $bashPath (Join-Path $sourceRoot 'configure') @options
    if ($LASTEXITCODE -ne 0) { throw 'FFmpeg configure failed' }
    (Get-Item -LiteralPath (Join-Path $sourceRoot 'ffbuild/version.sh')).LastWriteTimeUtc = [DateTime]::UtcNow
    & $bashPath -c 'exec "$@"' build $makePath "-j$Jobs" "REVISION=bili-remux-$($commit.Substring(0, 10))"
    if ($LASTEXITCODE -ne 0) { throw 'FFmpeg compilation failed' }
    $executable = Join-Path $compileDirectory 'ffmpeg.exe'
    $version = & $executable -version | Out-String
    if ($LASTEXITCODE -ne 0 -or $version -match '--enable-(gpl|nonfree)') { throw 'Unexpected FFmpeg version or license configuration' }
    $compiler = & gcc --version | Out-String
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read GCC version' }
    $staging = Join-Path $work 'package'
    New-Item -ItemType Directory -Path (Join-Path $staging 'bin') | Out-Null
    Copy-Item -LiteralPath $executable -Destination (Join-Path $staging 'bin/ffmpeg.exe')
    Copy-Item -LiteralPath (Join-Path $sourceRoot 'COPYING.LGPLv2.1') -Destination (Join-Path $staging 'LICENSE.txt')
    Copy-Item -LiteralPath $archive -Destination (Join-Path $staging 'FFmpeg-source-f044c5e627.tar.gz')
    $version | Set-Content -LiteralPath (Join-Path $staging 'BUILD-INFO.txt') -Encoding utf8
    [ordered]@{ commit = $commit; archive_sha256 = $sourceHash; source_url = "https://api.github.com/repos/FFmpeg/FFmpeg/tarball/$commit"; configure = $options; compiler = $compiler } |
        ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $staging 'SOURCE.json') -Encoding utf8
    New-Item -ItemType Directory -Path (Split-Path $output) -Force | Out-Null
    Move-Item -LiteralPath $staging -Destination $output
}
finally {
    $env:PATH = $previousPath
    Pop-Location
}
Write-Output "Built remux-only FFmpeg in $output"

#Requires -Version 7.0
param(
    [Parameter(Mandatory)][string]$Archive,
    [string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$archivePath = (Resolve-Path -LiteralPath $Archive).Path
$output = if ($OutputDirectory) { [IO.Path]::GetFullPath($OutputDirectory) } else { Join-Path $workspace 'vendor/ffmpeg' }
if (-not $output.StartsWith($workspace.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Output must stay inside the workspace' }
if (Test-Path -LiteralPath $output) { throw "Move the existing FFmpeg directory aside before preparing a new one: $output" }
$staging = Join-Path $workspace ('build/ffmpeg-extract/' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path (Join-Path $staging 'bin') -Force | Out-Null
$zip = [IO.Compression.ZipFile]::OpenRead($archivePath)
try {
    foreach ($entry in $zip.Entries) {
        $entryPath = $entry.FullName.Replace('\', '/')
        $destination = $null
        if ($entryPath -match '/bin/(ffmpeg\.exe|ffprobe\.exe|[^/]+\.dll)$') { $destination = Join-Path $staging ('bin/' + $Matches[1]) }
        if ($entryPath -match '(^|/)LICENSE\.txt$') { $destination = Join-Path $staging 'LICENSE.txt' }
        if ($destination) { [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $destination, $false) }
    }
    foreach ($required in @('bin/ffmpeg.exe', 'bin/ffprobe.exe', 'LICENSE.txt')) {
        if (-not (Test-Path -LiteralPath (Join-Path $staging $required))) { throw "Archive is missing $required. Partial extraction remains in $staging" }
    }
    New-Item -ItemType Directory -Path (Split-Path $output) -Force | Out-Null
    Move-Item -LiteralPath $staging -Destination $output
}
finally { $zip.Dispose() }
Write-Output "Prepared development FFmpeg and ffprobe in $output"

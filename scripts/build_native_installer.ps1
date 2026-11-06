param([string]$InnoCompiler)
$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $InnoCompiler) {
    $candidates = @(
        (Get-Command 'ISCC.exe' -ErrorAction SilentlyContinue).Source,
        (Join-Path $workspace 'build/tools/inno/ISCC.exe'),
        "$env:LOCALAPPDATA/Programs/Inno Setup 6/ISCC.exe",
        "${env:ProgramFiles(x86)}/Inno Setup 6/ISCC.exe"
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    if (-not $candidates) { throw 'Install Inno Setup 6 or specify -InnoCompiler' }
    $InnoCompiler = @($candidates)[0]
}
$project = Join-Path $workspace 'src/BiliDownloader.WinUI/BiliDownloader.WinUI.csproj'
$version = ([xml](Get-Content -LiteralPath $project -Raw)).Project.PropertyGroup.Version | Where-Object { $_ }
$fileVersion = ($version -split '-')[0]
$output = Join-Path $workspace 'dist/native-installer'
$appDirectory = Join-Path $workspace ('build/native-installer/app-' + [guid]::NewGuid().ToString('N'))
$ffmpeg = Join-Path $workspace 'vendor/ffmpeg-minimal'
foreach ($file in @('bin/ffmpeg.exe', 'LICENSE.txt', 'BUILD-INFO.txt', 'SOURCE.json', 'FFmpeg-source-f044c5e627.tar.gz')) {
    if (-not (Test-Path -LiteralPath (Join-Path $ffmpeg $file))) { throw "Missing release dependency $file. See docs/BUILDING.md" }
}
New-Item -ItemType Directory -Path $output -Force | Out-Null
& dotnet publish $project -c Release -p:Platform=x64 -p:UiRegression=false -o $appDirectory -v:minimal
if ($LASTEXITCODE -ne 0) { throw 'Production publish failed' }
foreach ($resource in @('BiliDownloader.exe', 'BiliDownloader.pri', 'App.xbf', 'MainWindow.xbf', 'ffmpeg.exe', 'Assets/brand.png')) {
    if (-not (Test-Path -LiteralPath (Join-Path $appDirectory $resource))) { throw "Published resource missing $resource" }
}
Copy-Item -LiteralPath (Join-Path $workspace 'LICENSE'), (Join-Path $workspace 'THIRD_PARTY_NOTICES.md') -Destination $appDirectory
$licenses = Join-Path $appDirectory 'licenses'
New-Item -ItemType Directory -Path $licenses -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $workspace 'installer/licenses/QRCoder-LICENSE.txt') -Destination $licenses
$assets = Get-Content -LiteralPath (Join-Path (Split-Path $project) 'obj/project.assets.json') -Raw | ConvertFrom-Json
$packageRoots = @($assets.packageFolders.PSObject.Properties.Name)
foreach ($library in $assets.libraries.PSObject.Properties) {
    if ($library.Value.type -ne 'package') { continue }
    $package = @($packageRoots | ForEach-Object { Join-Path $_ $library.Value.path } | Where-Object { Test-Path -LiteralPath $_ })[0]
    $notices = @(Get-ChildItem -LiteralPath $package -File | Where-Object Name -Match '(?i)^(license|notice|third.party)')
    if ($notices.Count -eq 0) { continue }
    $destination = Join-Path $licenses ($library.Name -replace '/', '-')
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    $notices | Copy-Item -Destination $destination
}
$runtime = Get-Content -LiteralPath (Join-Path $appDirectory 'BiliDownloader.runtimeconfig.json') -Raw | ConvertFrom-Json
$runtimeVersion = ($runtime.runtimeOptions.includedFrameworks | Where-Object name -eq 'Microsoft.NETCore.App').version
$runtimePackage = @($packageRoots | ForEach-Object { Join-Path $_ "microsoft.netcore.app.runtime.win-x64/$runtimeVersion" } | Where-Object { Test-Path -LiteralPath $_ })[0]
$runtimeNotices = Join-Path $licenses 'dotnet-runtime'
New-Item -ItemType Directory -Path $runtimeNotices -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $runtimePackage 'LICENSE.TXT'), (Join-Path $runtimePackage 'THIRD-PARTY-NOTICES.TXT') -Destination $runtimeNotices
$ffmpegNotices = Join-Path $licenses 'ffmpeg'
New-Item -ItemType Directory -Path $ffmpegNotices -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $ffmpeg 'LICENSE.txt'), (Join-Path $ffmpeg 'BUILD-INFO.txt'), (Join-Path $ffmpeg 'SOURCE.json') -Destination $ffmpegNotices

& $InnoCompiler '/DNativeClient=1' "/DMyAppVersion=$version" "/DMyAppFileVersion=$fileVersion" "/DAppSourceDir=$appDirectory" "/DAppOutputDir=$output" (Join-Path $workspace 'installer/BiliDownloader.iss')
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup compilation failed' }
$setup = Join-Path $output 'BiliDownloader-Setup.exe'
if ((Get-Item -LiteralPath $setup).Length -gt 100MB) { throw 'Native installer exceeds the 100 MiB size budget' }
$files = @(Get-ChildItem -LiteralPath $appDirectory -Recurse -File | Sort-Object FullName | ForEach-Object {
    [ordered]@{ path = [IO.Path]::GetRelativePath($appDirectory, $_.FullName); sha256 = (Get-FileHash -LiteralPath $_.FullName).Hash.ToLowerInvariant(); bytes = $_.Length }
})
$manifest = [ordered]@{
    version = $version
    source_commit = (& git -C $workspace rev-parse HEAD)
    application_directory = $appDirectory
    inno_compiler = [IO.Path]::GetFullPath($InnoCompiler)
    installer = $setup
    installer_sha256 = (Get-FileHash -LiteralPath $setup).Hash.ToLowerInvariant()
    files = $files
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $workspace 'build/native-installer/manifest.json') -Encoding utf8
Copy-Item -LiteralPath (Join-Path $workspace 'LICENSE'), (Join-Path $workspace 'THIRD_PARTY_NOTICES.md') -Destination $output -Force
Copy-Item -LiteralPath (Join-Path $ffmpeg 'LICENSE.txt') -Destination (Join-Path $output 'FFmpeg-LICENSE.txt') -Force
Copy-Item -LiteralPath (Join-Path $ffmpeg 'BUILD-INFO.txt') -Destination (Join-Path $output 'FFmpeg-build-info.txt') -Force
Copy-Item -LiteralPath (Join-Path $ffmpeg 'SOURCE.json') -Destination (Join-Path $output 'FFmpeg-source-info.json') -Force
Copy-Item -LiteralPath (Join-Path $ffmpeg 'FFmpeg-source-f044c5e627.tar.gz') -Destination $output -Force
Compress-Archive -Path $licenses -DestinationPath (Join-Path $output 'Runtime-Licenses.zip') -Force
[ordered]@{ version = $version; source_commit = $manifest.source_commit; dotnet_runtime = $runtimeVersion; architecture = 'win-x64'; payload_bytes = ($files | ForEach-Object { $_.bytes } | Measure-Object -Sum).Sum; files = $files } |
    ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $output 'BiliDownloader-build-info.json') -Encoding utf8
$checksums = Get-ChildItem -LiteralPath $output -File | Where-Object Name -ne 'SHA256SUMS.txt' | Sort-Object Name | ForEach-Object { (Get-FileHash -LiteralPath $_.FullName).Hash.ToLowerInvariant() + '  ' + $_.Name }
$checksums | Set-Content -LiteralPath (Join-Path $output 'SHA256SUMS.txt') -Encoding ascii
Write-Output "Built $setup ($([Math]::Round((Get-Item -LiteralPath $setup).Length / 1MB, 2)) MiB)"

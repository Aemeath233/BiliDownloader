param([string]$LegacyApplicationDirectory)
$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$manifest = Get-Content -LiteralPath (Join-Path $workspace 'build/native-installer/manifest.json') -Raw | ConvertFrom-Json
$testId = [guid]::NewGuid().ToString().ToUpperInvariant()
$testName = 'BiliDownloader Native Verification ' + $testId.Substring(0, 8)
$run = Join-Path $workspace ('build/native-installer-verification/' + $testId)
$nativeSetupDirectory = Join-Path $run 'native-setup'
$legacySetupDirectory = Join-Path $run 'legacy-setup'
$target = Join-Path $run '安装 空目录'
$profile = Join-Path $run 'legacy-profile'
$testRegistry = "HKCU:/Software/Microsoft/Windows/CurrentVersion/Uninstall/{$testId}_is1"
$productionRegistry = 'HKCU:/Software/Microsoft/Windows/CurrentVersion/Uninstall/{A845D3E4-8D4E-48A2-9D1E-6B59E248947D}_is1'
$productionBefore = Get-ItemProperty -LiteralPath $productionRegistry -ErrorAction SilentlyContinue
$shortcut = Join-Path ([Environment]::GetFolderPath('StartMenu')) "Programs/$testName/$testName.lnk"
$desktop = Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) "$testName.lnk"
$report = [ordered]@{ passed = $false; installer_sha256 = $manifest.installer_sha256; version = $manifest.version; isolated_install_identity = $testId; checks = @() }
New-Item -ItemType Directory -Path $run, $nativeSetupDirectory, $profile -Force | Out-Null
if (Test-Path -LiteralPath $testRegistry) { throw 'Test installation identity already exists' }
if ((Test-Path -LiteralPath $shortcut) -or (Test-Path -LiteralPath $desktop)) { throw 'Test shortcuts already exist' }
if ((Get-FileHash -LiteralPath $manifest.installer).Hash -ne $manifest.installer_sha256) { throw 'Production installer differs from the build manifest' }

function Invoke-InstallerProcess([string]$File, [string[]]$Arguments, [bool]$ExpectSuccess = $true) {
    $worker = Start-Process -FilePath $File -ArgumentList $Arguments -WindowStyle Hidden -PassThru
    try {
        if (-not $worker.WaitForExit(120000)) { $worker.Kill(); throw 'Installer process timed out' }
        if ($ExpectSuccess -and $worker.ExitCode -ne 0) { throw "Installer process failed with code $($worker.ExitCode)" }
        if (-not $ExpectSuccess -and $worker.ExitCode -eq 0) { throw 'Installer should have refused a running application' }
    }
    finally { $worker.Dispose() }
}
function Install-TestPackage([string]$Setup, [string]$Log) {
    Invoke-InstallerProcess $Setup @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', ('/DIR="' + $target + '"'), ('/GROUP="' + $testName + '"'), '/TASKS=desktopicon', ('/LOG="' + $Log + '"'))
    $installed = Get-ItemProperty -LiteralPath $testRegistry
    if ([IO.Path]::GetFullPath($installed.InstallLocation).TrimEnd('\') -ne $target) { throw 'Installer registered the wrong directory' }
    if (-not (Test-Path -LiteralPath $shortcut) -or -not (Test-Path -LiteralPath $desktop)) { throw 'Installation did not create both shortcuts' }
}
function Assert-NativePayload {
    foreach ($file in $manifest.files) {
        $installedFile = Join-Path $target $file.path
        if (-not (Test-Path -LiteralPath $installedFile) -or (Get-FileHash -LiteralPath $installedFile).Hash -ne $file.sha256) { throw "Installed payload mismatch $($file.path)" }
    }
}
function Uninstall-TestPackage([string]$Log) {
    $uninstaller = Join-Path $target 'unins000.exe'
    Invoke-InstallerProcess $uninstaller @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', ('/LOG="' + $Log + '"'))
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while ([DateTime]::UtcNow -lt $deadline -and ((Test-Path -LiteralPath $uninstaller) -or (Test-Path -LiteralPath $testRegistry))) { Start-Sleep -Milliseconds 100 }
    if ((Test-Path -LiteralPath $testRegistry) -or (Test-Path -LiteralPath $shortcut) -or (Test-Path -LiteralPath $desktop) -or (Test-Path -LiteralPath (Join-Path $target 'BiliDownloader.exe'))) { throw 'Uninstall left program registration, shortcuts or executable behind' }
}
try {
    $compilerArguments = @(
        ('/DMyAppId={{' + $testId + '}'),
        "/DMyAppName=$testName",
        "/DAppSourceDir=$($manifest.application_directory)",
        "/DAppOutputDir=$nativeSetupDirectory",
        '/DNativeClient=1',
        "/DMyAppVersion=$($manifest.version)",
        ('/DMyAppFileVersion=' + ($manifest.version -split '-')[0]),
        (Join-Path $workspace 'installer/BiliDownloader.iss')
    )
    & $manifest.inno_compiler @compilerArguments *> (Join-Path $run 'native-compile.log')
    if ($LASTEXITCODE -ne 0) { throw 'Test identity installer compilation failed' }
    $nativeSetup = Join-Path $nativeSetupDirectory 'BiliDownloader-Setup.exe'
    if (-not $LegacyApplicationDirectory -and $productionBefore.DisplayVersion -eq '2.2.0') { $LegacyApplicationDirectory = $productionBefore.InstallLocation }
    if ($LegacyApplicationDirectory) {
        $legacyPayload = Join-Path $run 'legacy-payload'
        New-Item -ItemType Directory -Path $legacyPayload, $legacySetupDirectory -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $LegacyApplicationDirectory 'BiliDownloader.exe') -Destination $legacyPayload
        Copy-Item -LiteralPath (Join-Path $LegacyApplicationDirectory '_internal') -Destination $legacyPayload -Recurse
        & $manifest.inno_compiler ('/DMyAppId={{' + $testId + '}') "/DMyAppName=$testName" "/DAppSourceDir=$legacyPayload" "/DAppOutputDir=$legacySetupDirectory" (Join-Path $workspace 'installer/BiliDownloader.iss') *> (Join-Path $run 'legacy-compile.log')
        if ($LASTEXITCODE -ne 0) { throw 'Legacy test identity installer compilation failed' }
        Install-TestPackage (Join-Path $legacySetupDirectory 'BiliDownloader-Setup.exe') (Join-Path $run 'legacy-install.log')
        if ((Get-ItemProperty -LiteralPath $testRegistry).DisplayVersion -ne '2.2.0') { throw 'Legacy package did not register version 2.2.0' }
        $report.checks += 'legacy_2_2_install'
    }
    New-Item -ItemType Directory -Path (Join-Path $target '_internal'), (Join-Path $target 'video') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $target '_internal/obsolete-test.dll') -Value 'owned test dependency'
    $video = Join-Path $target 'video/keep-test.mp4'
    Set-Content -LiteralPath $video -Value 'synthetic-download-sentinel'
    Set-Content -LiteralPath (Join-Path $profile 'keep-test.txt') -Value 'synthetic-user-data'
    @{ save_path = (Join-Path $run 'downloads'); max_concurrent_tasks = 4; steady_mode = $true } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $profile 'config.json') -Encoding utf8
    @{ session = @{ cookies = @{ SESSDATA = 'native-installer-test-only' }; refresh_token = ''; updated_at = 0 } } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $profile 'auth.json') -Encoding utf8
    '[]' | Set-Content -LiteralPath (Join-Path $profile 'tasks.json') -Encoding utf8
    Install-TestPackage $nativeSetup (Join-Path $run 'native-upgrade.log')
    Assert-NativePayload
    if (Test-Path -LiteralPath (Join-Path $target '_internal')) { throw 'Native upgrade did not clean old Python dependencies' }
    if ((Get-ItemProperty -LiteralPath $testRegistry).DisplayVersion -ne $manifest.version) { throw 'Upgrade registered the wrong version' }
    $report.checks += 'native_upgrade_payload_and_shortcuts'
    foreach ($index in 1..3) {
        & (Join-Path $PSScriptRoot 'verify_publish.ps1') -ApplicationDirectory $target -ProfileDirectory $profile -ReportDirectory (Join-Path $run "startup-$index")
    }
    foreach ($auth in @('auth.json', 'backup-before-winui3/auth.json')) {
        $contents = Get-Content -LiteralPath (Join-Path $profile $auth) -Raw
        if ($contents.Contains('native-installer-test-only') -or -not $contents.Contains('dpapi-current-user')) { throw 'Legacy authentication was not migrated to DPAPI' }
    }
    $report.checks += 'three_installed_launches_and_dpapi_migration'
    & dotnet run -c Release --project (Join-Path $workspace 'tests/native/BiliDownloader.Core.Tests.csproj') -- $workspace (Join-Path $run 'installed-core') (Join-Path $target 'ffmpeg.exe')
    if ($LASTEXITCODE -ne 0) { throw 'Core media tests using installed FFmpeg failed' }
    $report.checks += 'installed_ffmpeg_core_regression'
    $app = Start-Process -FilePath (Join-Path $target 'BiliDownloader.exe') -ArgumentList @('--profile', ('"' + $profile + '"')) -WindowStyle Hidden -PassThru
    try {
        $deadline = [DateTime]::UtcNow.AddSeconds(15)
        while ($app.MainWindowHandle -eq 0 -and -not $app.HasExited -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 100; $app.Refresh() }
        if ($app.MainWindowHandle -eq 0 -or $app.HasExited) { throw 'Protection test application failed to start' }
        Invoke-InstallerProcess $nativeSetup @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', ('/DIR="' + $target + '"'), ('/LOG="' + (Join-Path $run 'running-upgrade.log') + '"')) $false
        Invoke-InstallerProcess (Join-Path $target 'unins000.exe') @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', ('/LOG="' + (Join-Path $run 'running-uninstall.log') + '"')) $false
        $report.checks += 'running_application_blocks_upgrade_and_uninstall'
    }
    finally {
        if (-not $app.HasExited) { $null = $app.CloseMainWindow(); if (-not $app.WaitForExit(15000)) { $app.Kill(); $app.WaitForExit() } }
        $app.Dispose()
    }
    Install-TestPackage $nativeSetup (Join-Path $run 'native-reinstall.log')
    Assert-NativePayload
    if ((Get-Content -LiteralPath $video -Raw).Trim() -ne 'synthetic-download-sentinel' -or (Get-Content -LiteralPath (Join-Path $profile 'keep-test.txt') -Raw).Trim() -ne 'synthetic-user-data') { throw 'Upgrade changed user data' }
    $report.checks += 'native_overwrite_upgrade_preserves_data'
    Uninstall-TestPackage (Join-Path $run 'native-uninstall.log')
    if (-not (Test-Path -LiteralPath $video) -or -not (Test-Path -LiteralPath (Join-Path $profile 'auth.json'))) { throw 'Uninstall removed user data' }
    $report.checks += 'uninstall_removes_program_and_shortcuts_preserves_data'
    $target = Join-Path $run '全新安装'
    Install-TestPackage $nativeSetup (Join-Path $run 'fresh-install.log')
    Assert-NativePayload
    & (Join-Path $PSScriptRoot 'verify_publish.ps1') -ApplicationDirectory $target -ReportDirectory (Join-Path $run 'fresh-startup')
    Uninstall-TestPackage (Join-Path $run 'fresh-uninstall.log')
    $report.checks += 'fresh_install_startup_and_uninstall'
    $productionAfter = Get-ItemProperty -LiteralPath $productionRegistry -ErrorAction SilentlyContinue
    if ($productionBefore.DisplayVersion -ne $productionAfter.DisplayVersion -or $productionBefore.InstallLocation -ne $productionAfter.InstallLocation) { throw 'Production installation registration changed during isolated verification' }
    $report.passed = $true
}
catch { $report.error = $_.Exception.Message; throw }
finally {
    try {
        if (Test-Path -LiteralPath (Join-Path $target 'unins000.exe')) { Uninstall-TestPackage (Join-Path $run 'cleanup-uninstall.log') }
    }
    catch { $report.passed = $false; $report.cleanup_error = $_.Exception.Message }
    $report.completed_at = [DateTimeOffset]::UtcNow.ToString('o')
    $report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $run 'report.json') -Encoding utf8
}
if (-not $report.passed) { throw "Installer verification failed. See $run/report.json" }
$report | ConvertTo-Json -Depth 6
Write-Output "Installer verification saved to $run"

param([string]$ReportDirectory)
$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$output = if ($ReportDirectory) { [System.IO.Path]::GetFullPath($ReportDirectory) } else { Join-Path $workspace ('build/publish-verification/' + [guid]::NewGuid().ToString('N')) }
New-Item -ItemType Directory -Path $output -Force | Out-Null
$appDirectory = Join-Path $workspace ('build/winui-publish/' + [guid]::NewGuid().ToString('N'))
$profile = Join-Path $output ('profile-' + [guid]::NewGuid().ToString('N'))
$reportPath = Join-Path $output 'report.json'
$process = $null
$result = [ordered]@{ passed = $false; application_directory = $appDirectory; profile = $profile }
try {
    & dotnet publish (Join-Path $workspace 'src/BiliDownloader.WinUI/BiliDownloader.WinUI.csproj') -c Release -p:Platform=x64 -p:UiRegression=false -o $appDirectory -v:minimal
    if ($LASTEXITCODE -ne 0) { throw 'Production publish failed' }
    foreach ($resource in @('BiliDownloader.exe', 'BiliDownloader.pri', 'App.xbf', 'MainWindow.xbf', 'Assets/brand.png', 'logo.ico', 'ffmpeg.exe')) {
        if (-not (Test-Path -LiteralPath (Join-Path $appDirectory $resource))) { throw "Published resource missing $resource" }
    }
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $process = Start-Process -FilePath (Join-Path $appDirectory 'BiliDownloader.exe') -ArgumentList @('--profile', ('"' + $profile + '"')) -WorkingDirectory $appDirectory -WindowStyle Hidden -PassThru
    $ready = $false
    while ($watch.Elapsed.TotalSeconds -lt 30) {
        $process.Refresh()
        if ($process.HasExited) { throw "Published application exited before readiness, code $($process.ExitCode)" }
        $logs = @(Get-ChildItem -LiteralPath (Join-Path $profile 'logs') -Filter 'native-*.log' -ErrorAction SilentlyContinue)
        $ready = $process.MainWindowHandle -ne 0 -and @($logs | Select-String -Pattern '客户端已就绪').Count -gt 0
        if ($ready) { break }
        Start-Sleep -Milliseconds 100
    }
    if (-not $ready) { throw 'Published application did not initialize a window and runtime within 30 seconds' }
    $result.startup_to_window_and_runtime_ms = [Math]::Round($watch.Elapsed.TotalMilliseconds, 2)
    Start-Sleep -Milliseconds 1000
    $process.Refresh()
    if ($process.HasExited) { throw "Published application crashed after initialization, code $($process.ExitCode)" }
    if (Test-Path -LiteralPath (Join-Path $profile 'logs/startup-error.log')) { throw 'Published application recorded an unhandled exception' }
    if (-not $process.CloseMainWindow()) { throw 'Published application did not accept a normal close request' }
    if (-not $process.WaitForExit(15000)) { throw 'Published application did not exit within 15 seconds' }
    if ($process.ExitCode -ne 0) { throw "Published application exited with code $($process.ExitCode)" }
    $result.exit_code = $process.ExitCode
    $result.directory_bytes = (Get-ChildItem -LiteralPath $appDirectory -Recurse -File | Measure-Object Length -Sum).Sum
    $result.passed = $true
}
catch {
    $result.error = $_.Exception.Message
    throw
}
finally {
    if ($null -ne $process) {
        $process.Refresh()
        if (-not $process.HasExited) { $process.Kill(); $process.WaitForExit() }
        $process.Dispose()
    }
    $result.completed_at = [DateTimeOffset]::UtcNow.ToString('o')
    $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $reportPath -Encoding utf8
}
$result | Format-List
Write-Output "Production publish smoke report saved to $reportPath"

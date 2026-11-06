param([string]$ReportDirectory)
$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$output = if ($ReportDirectory) { [System.IO.Path]::GetFullPath($ReportDirectory) } else { Join-Path $workspace ('build/native-verification/' + [guid]::NewGuid().ToString('N')) }
New-Item -ItemType Directory -Path $output -Force | Out-Null
foreach ($dependency in @('vendor/ffmpeg/bin/ffmpeg.exe', 'vendor/ffmpeg/bin/ffprobe.exe', 'vendor/ffmpeg-minimal/bin/ffmpeg.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $workspace $dependency))) { throw "Missing test dependency $dependency. Prepare the FFmpeg development tools before running this suite." }
}
& dotnet run -c Release --project (Join-Path $workspace 'tests/native/BiliDownloader.Core.Tests.csproj') -- $workspace (Join-Path $output 'core')
if ($LASTEXITCODE -ne 0) { throw "Core tests failed. See $output/core/report.json" }
& (Join-Path $PSScriptRoot 'verify_winui.ps1') -Experience -Configuration Release -ReportDirectory (Join-Path $output 'ui')
& (Join-Path $PSScriptRoot 'verify_publish.ps1') -ReportDirectory (Join-Path $output 'publish')
$auditPath = Join-Path $output 'dependency-audit.json'
& dotnet list (Join-Path $workspace 'src/BiliDownloader.WinUI/BiliDownloader.WinUI.csproj') package --vulnerable --include-transitive --format json | Set-Content -LiteralPath $auditPath -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw 'NuGet dependency audit failed to run' }
$audit = Get-Content -LiteralPath $auditPath -Raw | ConvertFrom-Json
$advisories = @($audit.projects.frameworks.topLevelPackages.vulnerabilities) + @($audit.projects.frameworks.transitivePackages.vulnerabilities)
if (@($advisories | Where-Object { $null -ne $_ }).Count -gt 0) { throw "NuGet reported known vulnerabilities. See $auditPath" }
$core = Get-Content -LiteralPath (Join-Path $output 'core/report.json') -Raw | ConvertFrom-Json
$ui = Get-Content -LiteralPath (Join-Path $output 'ui/report.json') -Raw | ConvertFrom-Json
$experience = Get-Content -LiteralPath (Join-Path $output 'ui/experience-report.json') -Raw | ConvertFrom-Json
$publish = Get-Content -LiteralPath (Join-Path $output 'publish/report.json') -Raw | ConvertFrom-Json
$navigation = @($experience.timings | Where-Object action -eq 'navigation' | ForEach-Object elapsed_ms | Sort-Object)
$search = @($experience.timings | Where-Object action -eq 'queue_search' | ForEach-Object elapsed_ms)
$summary = [ordered]@{
    passed = $core.passed -and $ui.passed -and $experience.passed -and $publish.passed
    completed_at = [DateTimeOffset]::UtcNow.ToString('o')
    core_cases = $core.cases.Count
    ui_groups = @($ui.results | Where-Object stage -ne 'experience_audit').Count + $experience.checks.Count
    navigation_samples = $navigation.Count
    navigation_median_ms = [Math]::Round($navigation[[int](($navigation.Count - 1) * 0.5)], 2)
    navigation_p95_ms = [Math]::Round($navigation[[int](($navigation.Count - 1) * 0.95)], 2)
    navigation_max_ms = [Math]::Round($navigation[-1], 2)
    queue_search_max_ms = [Math]::Round(($search | Measure-Object -Maximum).Maximum, 2)
    startup_to_ready_ms = $ui.results[0].startup_to_ready_ms
    display_scale = $ui.results[0].scale
    known_nuget_advisories = 0
    production_publish_smoke_passed = $publish.passed
    production_directory_bytes = $publish.directory_bytes
    report_directory = $output
    measurement_scope = 'Release build, isolated profiles and synthetic HTTP. Dispatch and layout timing, not GPU frame presentation. Includes native accessibility peers, local real FFmpeg processing and screenshots.'
}
$summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $output 'summary.json') -Encoding utf8
$summary | Format-List
Write-Output "Detailed reports and screenshots saved to $output"

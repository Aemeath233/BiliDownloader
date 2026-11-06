param([switch]$Experience, [ValidateSet('Debug', 'Release')][string]$Configuration = 'Release', [string]$ReportDirectory)
$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$output = Join-Path $workspace 'build/winui-regression'
$appDirectory = Join-Path $output ('test-app-' + [guid]::NewGuid().ToString('N'))
$project = Join-Path $workspace 'src/BiliDownloader.WinUI/BiliDownloader.WinUI.csproj'
& dotnet build $project -c $Configuration -p:Platform=x64 -p:UiRegression=true -o $appDirectory -v:minimal
if ($LASTEXITCODE -ne 0) { throw 'WinUI regression build failed' }
$runDirectory = if ($ReportDirectory) { [System.IO.Path]::GetFullPath($ReportDirectory) } else { Join-Path $output ('runs/' + [guid]::NewGuid().ToString('N')) }
New-Item -ItemType Directory -Path $runDirectory -Force | Out-Null
$report = Join-Path $runDirectory 'report.json'
if (Test-Path -LiteralPath $report) { Move-Item -LiteralPath $report -Destination ($report + '.previous-' + [guid]::NewGuid().ToString('N')) }
$arguments = @('--report', ('"' + $report + '"'))
if ($Experience) { $arguments += '--experience' }
$process = Start-Process -FilePath (Join-Path $appDirectory 'BiliDownloader.exe') -ArgumentList $arguments -WindowStyle Hidden -PassThru
if (-not $process.WaitForExit(180000)) { throw 'WinUI regression timed out' }
if (-not (Test-Path -LiteralPath $report)) { throw "WinUI exited with code $($process.ExitCode) before producing a report" }
$result = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
if (-not $result.passed) { throw $result.error }
if ($process.ExitCode -ne 0) { throw "WinUI exited with code $($process.ExitCode)" }
$resolvedApp = (Resolve-Path -LiteralPath $appDirectory).Path
$resolvedOutput = [System.IO.Path]::GetFullPath($output).TrimEnd('\') + '\'
if (-not $resolvedApp.StartsWith($resolvedOutput, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Test app cleanup target escaped the build directory' }
Remove-Item -LiteralPath $resolvedApp -Recurse -Force
$result.results | Format-Table -AutoSize
Write-Output "Passed. Report saved to $report"

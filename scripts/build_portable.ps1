$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $ProjectRoot
try {
    $UvCommand = Get-Command "uv.exe" -ErrorAction SilentlyContinue
    if ($UvCommand) {
        $BuildPython = $UvCommand.Source
        $BuildPrefix = @("run", "--no-project", "--python", "3.11", "--with-requirements", "requirements-build.txt", "python")
    } else {
        $BuildPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $BuildPython)) {
            $BuildPython = (Get-Command "python.exe" -ErrorAction Stop).Source
        }
        $BuildPrefix = @()
    }

    function Invoke-BuildPython {
        param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
        & $BuildPython @BuildPrefix @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Build command failed with exit code $LASTEXITCODE"
        }
    }

    if (-not (Test-Path -LiteralPath "vendor\ffmpeg\bin\ffmpeg.exe") -or -not (Test-Path -LiteralPath "vendor\ffmpeg\LICENSE.txt")) {
        Invoke-BuildPython "scripts\prepare_ffmpeg.py"
    }
    Invoke-BuildPython "-m" "PyInstaller" "--noconfirm" "--distpath" "dist\portable" "--workpath" "build\portable" "portable.spec"
    Write-Host "Portable executable: $ProjectRoot\dist\portable\BiliDownloader-Portable.exe"
} finally {
    Pop-Location
}

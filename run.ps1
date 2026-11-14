param([switch]$Gui)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$startupLog = Join-Path $PSScriptRoot 'startup.log'
try {
    $localPython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
    if (Test-Path $localPython) {
        $basePython = $localPython
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        $resolved = & py -3 -c 'import sys; print(sys.executable)'
        if ($LASTEXITCODE -ne 0) { throw 'Python discovery failed.' }
        $basePython = ($resolved | Select-Object -Last 1).Trim()
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $resolved = & python -c 'import sys; print(sys.executable)'
        if ($LASTEXITCODE -ne 0) { throw 'Python discovery failed.' }
        $basePython = ($resolved | Select-Object -Last 1).Trim()
    } else {
        throw 'Python 3.10 or newer is required.'
    }
    # Always use the already installed base interpreter. Do not execute .venv.
    if ($Gui) {
        & $basePython -I launch.py --gui
        if ($LASTEXITCODE -ne 0) { throw "Startup failed. Details: $startupLog" }
        exit 0
    }
    Write-Host "Base Python: $basePython"
    & $basePython -I launch.py
    if ($LASTEXITCODE -ne 0) { throw "Startup failed. Details: $startupLog" }
} catch {
    Write-Host $_ -ForegroundColor Red
    Write-Host 'Do not delete your projects or change antivirus protection settings.'
    if ($Gui) {
        Add-Content -LiteralPath $startupLog -Encoding UTF8 -Value ('Launcher: ' + $_)
        Add-Type -AssemblyName System.Windows.Forms
        [void][System.Windows.Forms.MessageBox]::Show("Ship Architect startup failed. See $startupLog", "Ship Architect")
    } else { Read-Host 'Press Enter to close' }
    exit 1
}

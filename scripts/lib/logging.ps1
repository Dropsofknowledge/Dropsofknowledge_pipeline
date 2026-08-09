# lib/logging.ps1
# Logging helpers. Writes to console and to a per-run log file.
# Keep launcher errors separate from render errors (spec 18).

Set-StrictMode -Version Latest

function New-DokLogger {
    param(
        [Parameter(Mandatory)][string]$LogDir,
        [string]$Name = "render"
    )
    if (-not (Test-Path -LiteralPath $LogDir)) {
        New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    }
    $stamp = (Get-Date).ToString("yyyyMMdd_HHmmss")
    $logFile = Join-Path $LogDir "$Name`_$stamp.log"
    [pscustomobject]@{
        File = $logFile
        Name = $Name
    }
}

function Write-DokLog {
    param(
        [Parameter(Mandatory)]$Logger,
        [Parameter(Mandatory)][string]$Message,
        [ValidateSet('INFO','WARN','ERROR','OK','STEP')]
        [string]$Level = 'INFO'
    )
    $ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $line = "[{0}] [{1,-5}] {2}" -f $ts, $Level, $Message

    switch ($Level) {
        'ERROR' { Write-Host $line -ForegroundColor Red }
        'WARN'  { Write-Host $line -ForegroundColor Yellow }
        'OK'    { Write-Host $line -ForegroundColor Green }
        'STEP'  { Write-Host $line -ForegroundColor Cyan }
        default { Write-Host $line }
    }

    if ($Logger -and $Logger.File) {
        # Never let logging crash the pipeline.
        try { Add-Content -LiteralPath $Logger.File -Value $line -Encoding utf8 } catch { }
    }
}

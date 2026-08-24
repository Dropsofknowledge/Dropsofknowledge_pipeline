<#
.SYNOPSIS
    Import Lecture wizard (spec 7). Asks the minimum: series, episode, source folder.
#>
param(
    [Parameter(Mandatory)][string]$RootDir,
    [string]$Series,
    [string]$Episode,
    [string]$SourceFolder
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$lib = Join-Path $RootDir 'scripts/lib'
. (Join-Path $lib 'logging.ps1')
. (Join-Path $lib 'production.ps1')
. (Join-Path $lib 'audio.ps1')

if (-not $Series)       { $Series       = Read-Host 'Series name (e.g. Kabair)' }
if (-not $Episode)      { $Episode      = Read-Host 'Episode number (e.g. 0048)' }
if (-not $SourceFolder) { $SourceFolder = Read-Host 'Lecture source folder path' }

$SourceFolder = $SourceFolder.Trim('"').Trim()
if (-not (Test-Path -LiteralPath $SourceFolder)) {
    Write-Host "Source folder does not exist: $SourceFolder" -ForegroundColor Red
    exit 2
}

try {
    $res = Import-DokLecture -RootDir $RootDir -Series $Series -Episode $Episode -SourceFolder $SourceFolder
    Write-Host ""
    Write-Host "Project created: $($res.ProjectDir)" -ForegroundColor Green
    Write-Host "Project id:      $($res.ProjectId)"
    Write-Host "Files copied:    $((@($res.Copied)) -join ', ')"
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Cyan
    Write-Host "  1. Place/generate clip_plan.json and, if needed, layout.json / background.* in the source folder."
    Write-Host "  2. Double-click RUN_PROJECT.cmd in that folder to render."
} catch {
    Write-Host "Import failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

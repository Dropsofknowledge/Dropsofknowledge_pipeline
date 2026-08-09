<#
.SYNOPSIS
    Interactive menu backend for START_HERE.cmd (spec 8).
#>
param([Parameter(Mandatory)][string]$RootDir)
Set-StrictMode -Version Latest
$RootDir = (Resolve-Path -LiteralPath $RootDir).Path

function Show-Menu {
    Write-Host ""
    Write-Host "==============================================" -ForegroundColor Green
    Write-Host "        DropsofKnowledge Renderer" -ForegroundColor Green
    Write-Host "==============================================" -ForegroundColor Green
    Write-Host "  1. Import Lecture"
    Write-Host "  2. Render Existing Project"
    Write-Host "  3. Dashboard"
    Write-Host "  4. Exit"
    Write-Host ""
}

while ($true) {
    Show-Menu
    $choice = Read-Host 'Choose an option (1-4)'
    switch ($choice) {
        '1' {
            & (Join-Path $RootDir 'scripts/new_episode.ps1') -RootDir $RootDir
        }
        '2' {
            $proj = Read-Host 'Project folder path'
            $proj = $proj.Trim('"').Trim()
            if (Test-Path -LiteralPath $proj) {
                & (Join-Path $RootDir 'scripts/render_project.ps1') -ProjectRoot $proj -RootDir $RootDir
            } else { Write-Host "Folder not found: $proj" -ForegroundColor Red }
        }
        '3' {
            & (Join-Path $RootDir 'scripts/dashboard.ps1') -RootDir $RootDir
        }
        '4' { break }
        default { Write-Host "Invalid choice." -ForegroundColor Yellow }
    }
}

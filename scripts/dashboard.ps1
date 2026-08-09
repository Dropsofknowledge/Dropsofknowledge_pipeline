<#
.SYNOPSIS
    Project / root dashboard (spec 16). Shows planned/completed/failed/pending.
#>
param(
    [Parameter(Mandatory)][string]$RootDir,
    [string]$ProjectRoot   # if omitted, summarise every project under Projects/
)
Set-StrictMode -Version Latest
$lib = Join-Path $RootDir 'scripts/lib'
. (Join-Path $lib 'state.ps1')

function Show-DokProjectDashboard {
    param([string]$Dir)
    $planPath  = Join-Path $Dir 'clip_plan.json'
    $statePath = Join-Path $Dir 'render_state.json'
    if (-not (Test-Path -LiteralPath $planPath)) { return }
    $plan = Get-Content -LiteralPath $planPath -Raw -Encoding utf8 | ConvertFrom-Json
    $planned = @($plan.clips).Count
    $completed = 0; $failed = 0; $lastRun = 'never'
    $statuses = @{}
    if (Test-Path -LiteralPath $statePath) {
        $st = Get-Content -LiteralPath $statePath -Raw -Encoding utf8 | ConvertFrom-Json
        if ($st.PSObject.Properties.Name -contains 'last_run' -and $st.last_run) { $lastRun = $st.last_run }
        foreach ($c in @($st.clips)) { $statuses[[string]$c.id] = $c.status }
        $completed = @($st.clips | Where-Object { $_.status -eq 'completed' }).Count
        $failed    = @($st.clips | Where-Object { $_.status -eq 'failed' }).Count
    }
    $pending = $planned - $completed - $failed
    if ($pending -lt 0) { $pending = 0 }

    Write-Host ("-" * 52)
    Write-Host ("Project : {0}" -f (Split-Path $Dir -Leaf)) -ForegroundColor Cyan
    Write-Host ("Series  : {0}   Episode: {1}" -f $plan.series, $plan.episode)
    Write-Host ("Planned : {0,3}   Completed: {1,3}   Failed: {2,3}   Pending: {3,3}" -f $planned,$completed,$failed,$pending)
    Write-Host ("Last run: {0}" -f $lastRun)
    if ($failed -gt 0) { Write-Host ("Warnings: {0} clip(s) need attention" -f $failed) -ForegroundColor Yellow }
}

if ($ProjectRoot) {
    Show-DokProjectDashboard -Dir (Resolve-Path -LiteralPath $ProjectRoot).Path
} else {
    $projects = Join-Path $RootDir 'Projects'
    Write-Host "DropsofKnowledge - All Projects" -ForegroundColor Green
    if (Test-Path -LiteralPath $projects) {
        $dirs = Get-ChildItem -LiteralPath $projects -Directory -ErrorAction SilentlyContinue
        if (@($dirs).Count -eq 0) { Write-Host "(no projects yet)" }
        foreach ($d in $dirs) { Show-DokProjectDashboard -Dir $d.FullName }
    } else { Write-Host "(no Projects folder yet)" }
    Write-Host ("-" * 52)
}

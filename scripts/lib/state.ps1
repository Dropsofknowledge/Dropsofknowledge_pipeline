# lib/state.ps1
# Runtime status + resume behaviour (render_state.json).
# Keep runtime status SEPARATE from the editorial clip_plan.json (spec 23).

Set-StrictMode -Version Latest

function Get-DokStatePath {
    param([Parameter(Mandatory)][string]$ProjectRoot)
    Join-Path $ProjectRoot 'render_state.json'
}

# Load existing state, or initialise an empty one keyed to the project id.
function Get-DokState {
    param(
        [Parameter(Mandatory)][string]$ProjectRoot,
        [Parameter(Mandatory)][string]$ProjectId
    )
    $path = Get-DokStatePath -ProjectRoot $ProjectRoot
    if (Test-Path -LiteralPath $path) {
        try {
            $raw = Get-Content -LiteralPath $path -Raw -Encoding utf8
            if (-not [string]::IsNullOrWhiteSpace($raw)) {
                $obj = $raw | ConvertFrom-Json
                # Normalise into a hashtable keyed by clip id for fast lookup.
                $clips = @{}
                if ($obj.PSObject.Properties.Name -contains 'clips' -and $obj.clips) {
                    foreach ($c in @($obj.clips)) { $clips[[string]$c.id] = $c }
                }
                return [pscustomobject]@{
                    project  = $ProjectId
                    last_run = $obj.last_run
                    Clips    = $clips
                }
            }
        } catch {
            Write-Warning "render_state.json unreadable, starting fresh: $($_.Exception.Message)"
        }
    }
    [pscustomobject]@{ project = $ProjectId; last_run = $null; Clips = @{} }
}

function Get-DokClipStatus {
    param([Parameter(Mandatory)]$State,[Parameter(Mandatory)][string]$ClipId)
    if ($State.Clips.ContainsKey($ClipId)) { return $State.Clips[$ClipId].status }
    return 'pending'
}

function Set-DokClipStatus {
    param(
        [Parameter(Mandatory)]$State,
        [Parameter(Mandatory)][string]$ClipId,
        [Parameter(Mandatory)][ValidateSet('pending','completed','failed','rendering')][string]$Status,
        [string]$Output,
        [string]$Reason
    )
    $entry = [ordered]@{ id = $ClipId; status = $Status }
    if ($Output) { $entry.output = $Output }
    if ($Reason) { $entry.reason = $Reason }
    $State.Clips[$ClipId] = [pscustomobject]$entry
}

# Persist state atomically (write temp then move) so a crash never corrupts it.
function Save-DokState {
    param([Parameter(Mandatory)][string]$ProjectRoot,[Parameter(Mandatory)]$State)
    $path = Get-DokStatePath -ProjectRoot $ProjectRoot
    $ordered = $State.Clips.Keys | Sort-Object
    $clipList = foreach ($k in $ordered) { $State.Clips[$k] }
    $doc = [ordered]@{
        project  = $State.project
        last_run = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        clips    = @($clipList)
    }
    $tmp = "$path.tmp"
    ($doc | ConvertTo-Json -Depth 8) | Set-Content -LiteralPath $tmp -Encoding utf8
    Move-Item -LiteralPath $tmp -Destination $path -Force
}

# lib/validation.ps1
# Pre-flight validation (spec 11). Reports clear messages and never crashes
# on empty collections.

Set-StrictMode -Version Latest

# Convert "HH:MM:SS.mmm" / "MM:SS" / seconds into total seconds (double).
function ConvertTo-DokSeconds {
    param([Parameter(Mandatory)]$Value)
    if ($null -eq $Value) { return $null }
    if ($Value -is [double] -or $Value -is [int] -or $Value -is [long]) { return [double]$Value }
    $s = [string]$Value

    # Accept both comma and dot decimals from transcripts / clip plans.
    $s = $s.Trim().Replace(',', '.')
    if ($s -match '^[0-9]+(\.[0-9]+)?$') { return [double]$s }

    $parts = $s.Split(':')
    if ($parts.Count -lt 1 -or $parts.Count -gt 3) { return $null }

    [double]$total = 0
    foreach ($p in $parts) {
        $p = $p.Trim().Replace(',', '.')
        if (-not ($p -match '^[0-9]+(\.[0-9]+)?$')) { return $null }
        $total = $total * 60 + [double]$p
    }
    return $total
}

function New-DokIssue {
    param([string]$Severity,[string]$Message,[string]$ClipId)
    [pscustomobject]@{ severity = $Severity; message = $Message; clip = $ClipId }
}

# PowerShell 5.1 + Set-StrictMode can treat an empty Generic.List as one object
# when it is piped to Where-Object. This helper always gives a normal array of
# issue objects and never returns the List container itself.
function ConvertTo-DokIssueArray {
    param($Issues)
    if ($null -eq $Issues) { return @() }

    if ($Issues -is [System.Collections.Generic.List[object]]) {
        if ($Issues.Count -eq 0) { return @() }
        return @($Issues.ToArray())
    }

    if ($Issues -is [System.Collections.IEnumerable] -and -not ($Issues -is [string])) {
        $out = New-Object System.Collections.Generic.List[object]
        foreach ($i in $Issues) {
            if ($null -ne $i -and ($i.PSObject.Properties.Name -contains 'severity')) { $out.Add($i) }
        }
        if ($out.Count -eq 0) { return @() }
        return @($out.ToArray())
    }

    if ($Issues.PSObject.Properties.Name -contains 'severity') { return @($Issues) }
    return @()
}

# Returns an array of issue objects (possibly empty). Severity 'error' blocks render.
function Test-DokProject {
    param(
        [Parameter(Mandatory)][string]$ProjectRoot,
        [Parameter(Mandatory)]$Paths,        # resolved-paths object from production.ps1
        $ClipPlan,                           # parsed clip plan (may be $null)
        [double]$SourceDuration = 0
    )
    $issues = New-Object System.Collections.Generic.List[object]

    if (-not (Test-Path -LiteralPath $ProjectRoot)) {
        $issues.Add((New-DokIssue 'error' "Project root not found: $ProjectRoot"))
        return (ConvertTo-DokIssueArray $issues)
    }
    if (-not $Paths.Audio)      { $issues.Add((New-DokIssue 'error' 'No audio file found.')) }
    if (-not $Paths.Transcript) { $issues.Add((New-DokIssue 'warn'  'No transcript found (captions may be empty).')) }
    if (-not $Paths.Background)  { $issues.Add((New-DokIssue 'warn'  'No background found; default background will be used.')) }
    if (-not $Paths.ClipPlan)    { $issues.Add((New-DokIssue 'error' 'clip_plan.json missing.')) }
    if (-not $Paths.Template)    { $issues.Add((New-DokIssue 'error' 'Template layout.json missing or unparseable.')) }
    if (-not $Paths.OutputWritable) { $issues.Add((New-DokIssue 'error' 'Output directory is not writable.')) }

    if ($null -ne $ClipPlan -and ($ClipPlan.PSObject.Properties.Name -contains 'clips')) {
        $clips = @($ClipPlan.clips)   # @() guards against single object / null
        if ($clips.Count -eq 0) {
            $issues.Add((New-DokIssue 'warn' 'Clip plan contains zero clips.'))
        }
        foreach ($clip in $clips) {
            $id = [string]$clip.id
            $start = ConvertTo-DokSeconds $clip.start
            $end   = ConvertTo-DokSeconds $clip.end
            if ($null -eq $start) { $issues.Add((New-DokIssue 'error' "Invalid start timestamp." $id)); continue }
            if ($null -eq $end)   { $issues.Add((New-DokIssue 'error' "Invalid end timestamp." $id)); continue }
            if ($end -le $start)  { $issues.Add((New-DokIssue 'error' "End time is not after start time." $id)) }
            if ($SourceDuration -gt 0 -and $end -gt ($SourceDuration + 0.5)) {
                $issues.Add((New-DokIssue 'error' "Timestamp exceeds source duration ($([math]::Round($SourceDuration,1))s)." $id))
            }
        }
    }
    return (ConvertTo-DokIssueArray $issues)
}

function Get-DokErrorCount {
    param($Issues)
    $count = 0
    foreach ($i in (ConvertTo-DokIssueArray $Issues)) {
        if (($i.PSObject.Properties.Name -contains 'severity') -and $i.severity -eq 'error') { $count++ }
    }
    return $count
}

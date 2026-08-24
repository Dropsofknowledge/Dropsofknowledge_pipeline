<#
.SYNOPSIS
    Render every clip in a project's clip_plan.json (spec 10, 17).
.DESCRIPTION
    Idempotent batch renderer. Skips completed clips, resumes pending/failed,
    continues past failures, writes preview/report/manifest per clip and a
    final summary. Project root and Dok root are passed explicitly.
#>
param(
    [Parameter(Mandatory)][string]$ProjectRoot,
    [Parameter(Mandatory)][string]$RootDir,
    [switch]$Force   # re-render even completed clips
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# --- Load modules -----------------------------------------------------------
$lib = Join-Path $RootDir 'scripts/lib'
. (Join-Path $lib 'logging.ps1')
. (Join-Path $lib 'state.ps1')
. (Join-Path $lib 'validation.ps1')
. (Join-Path $lib 'production.ps1')
. (Join-Path $lib 'layout.ps1')
. (Join-Path $lib 'captions.ps1')
. (Join-Path $lib 'audio.ps1')
. (Join-Path $lib 'ffmpeg.ps1')

$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$RootDir     = (Resolve-Path -LiteralPath $RootDir).Path


function Get-DokErrorText {
    param($ErrorRecord)
    try {
        $parts = New-Object System.Collections.Generic.List[string]
        if ($ErrorRecord.Exception -and -not [string]::IsNullOrWhiteSpace($ErrorRecord.Exception.Message)) { $parts.Add($ErrorRecord.Exception.Message) }
        $asText = ($ErrorRecord | Out-String).Trim()
        if (-not [string]::IsNullOrWhiteSpace($asText)) { $parts.Add($asText) }
        if ($ErrorRecord.ScriptStackTrace) { $parts.Add($ErrorRecord.ScriptStackTrace) }
        if ($ErrorRecord.InvocationInfo -and $ErrorRecord.InvocationInfo.PositionMessage) { $parts.Add($ErrorRecord.InvocationInfo.PositionMessage) }
        $joined = (($parts.ToArray() | Select-Object -Unique) -join "`n")
        if ([string]::IsNullOrWhiteSpace($joined)) { return 'Unknown error. Check the latest logs/render_*.log file.' }
        return $joined
    } catch {
        return 'Unknown error while formatting exception.'
    }
}

$logger = New-DokLogger -LogDir (Join-Path $ProjectRoot 'logs') -Name 'render'
Write-DokLog $logger "DropsofKnowledge Renderer starting" 'STEP'
Write-DokLog $logger "Project: $ProjectRoot"

# --- 1. Load clip plan ------------------------------------------------------
$planPath = Join-Path $ProjectRoot 'clip_plan.json'
if (-not (Test-Path -LiteralPath $planPath)) {
    $legacyPlanPath = Join-Path $ProjectRoot 'clip.json'
    if (Test-Path -LiteralPath $legacyPlanPath) {
        Copy-Item -LiteralPath $legacyPlanPath -Destination $planPath -Force
        Write-DokLog $logger "clip.json found; normalized it to clip_plan.json." 'INFO'
    } else {
        Write-DokLog $logger "clip_plan.json not found." 'ERROR'
        exit 2
    }
}
$plan = Get-Content -LiteralPath $planPath -Raw -Encoding utf8 | ConvertFrom-Json
$series  = if ($plan.PSObject.Properties.Name -contains 'series')  { [string]$plan.series }  else { '' }
$episode = if ($plan.PSObject.Properties.Name -contains 'episode') { [string]$plan.episode } else { '' }
$speaker = if ($plan.PSObject.Properties.Name -contains 'speaker') { [string]$plan.speaker } else { '' }
$projId  = Get-DokProjectId -Series $series -Episode $episode

# --- 2-3. Resolve tools + paths --------------------------------------------
try { $tools = Get-DokTools -RootDir $RootDir }
catch { Write-DokLog $logger $_.Exception.Message 'ERROR'; exit 3 }

$paths = Resolve-DokPaths -ProjectRoot $ProjectRoot -RootDir $RootDir -Series $series
$srcDuration = if ($paths.Audio) { Get-DokAudioDuration -FfprobeExe $tools.Ffprobe -Path $paths.Audio } else { 0 }

# --- 4. Validate ------------------------------------------------------------
$issues = Test-DokProject -ProjectRoot $ProjectRoot -Paths $paths -ClipPlan $plan -SourceDuration $srcDuration
foreach ($i in $issues) {
    $lvl = switch ($i.severity) { 'error' { 'ERROR' } 'warn' { 'WARN' } default { 'INFO' } }
    $msg = if ($i.clip) { "[clip $($i.clip)] $($i.message)" } else { $i.message }
    Write-DokLog $logger $msg $lvl
}
if ((Get-DokErrorCount $issues) -gt 0) {
    # Project-level errors abort; clip-level errors are recorded then skipped.
    $projErrors = @($issues | Where-Object { $_.severity -eq 'error' -and -not $_.clip })
    if ($projErrors.Count -gt 0) { Write-DokLog $logger "Validation failed; aborting." 'ERROR'; exit 4 }
}

# --- 5. Load template + working audio --------------------------------------
$layout = Get-DokLayout -LayoutPath $paths.Template
$cw = [int]$layout.canvas.width; $ch = [int]$layout.canvas.height
$workAudio = Resolve-DokWorkingAudio -FfmpegExe $tools.Ffmpeg -SourceAudio $paths.Audio -CacheDir $paths.CacheDir
$allCues = Read-DokTranscript -Path $paths.Transcript

# --- 6. Load/init state -----------------------------------------------------
$state = Get-DokState -ProjectRoot $ProjectRoot -ProjectId $projId

# Ensure required project folders exist before any writes happen.
foreach ($dir in @($paths.OutputDir, $paths.ReportsDir, $paths.CacheDir, $paths.LogsDir)) {
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
}


# Clip ids that have a fatal validation error -> mark failed, never attempt.
$badClipIds = @{}
foreach ($i in $issues) { if ($i.severity -eq 'error' -and $i.clip) { $badClipIds[$i.clip] = $i.message } }

$clips = @($plan.clips)
$total = $clips.Count
$done = 0; $failed = 0; $skipped = 0
Write-DokLog $logger "Planned clips: $total" 'INFO'

# --- 7. Per-clip loop -------------------------------------------------------
foreach ($clip in $clips) {
    $id = [string]$clip.id
    $clipOut = Join-Path $paths.OutputDir $id
    $mp4 = Join-Path $clipOut 'clip.mp4'

    $status = Get-DokClipStatus -State $state -ClipId $id
    if ($status -eq 'completed' -and -not $Force -and (Test-Path -LiteralPath $mp4)) {
        Write-DokLog $logger "Clip $id already completed - skipping." 'INFO'
        $skipped++; continue
    }
    if ($badClipIds.ContainsKey($id)) {
        Write-DokLog $logger "Clip $id invalid: $($badClipIds[$id])" 'ERROR'
        Set-DokClipStatus -State $state -ClipId $id -Status 'failed' -Reason $badClipIds[$id]
        Save-DokState -ProjectRoot $ProjectRoot -State $state
        $failed++; continue
    }

    try {
        Write-DokLog $logger "Rendering clip $id - $($clip.headline)" 'STEP'
        Set-DokClipStatus -State $state -ClipId $id -Status 'rendering'
        if (-not (Test-Path -LiteralPath $clipOut)) { New-Item -ItemType Directory -Force -Path $clipOut | Out-Null }

        $start = ConvertTo-DokSeconds $clip.start
        $end   = ConvertTo-DokSeconds $clip.end
        $dur   = $end - $start

        # Build overlay -> base frame.
        # The baked series label should show the project/episode code (e.g. 0486),
        # not the per-clip sequence id (0001, 0002, ...).
        $svg = New-DokOverlaySvg -Layout $layout -Headline $clip.headline -Speaker $speaker -Series $series -ClipId $episode -RootDir $RootDir
        $overlayPng = Join-Path $clipOut 'overlay.png'
        $baseFrame  = Join-Path $clipOut 'base.png'
        if (-not (Convert-DokSvgToPng -MagickExe $tools.Magick -Svg $svg -OutPng $overlayPng -Width $cw -Height $ch)) {
            throw "Failed to rasterise overlay."
        }
        if (-not (New-DokBaseFrame -MagickExe $tools.Magick -Background $paths.Background -OverlayPng $overlayPng -OutPng $baseFrame -Width $cw -Height $ch)) {
            throw "Failed to build base frame."
        }

        # Captions.
        $ass = Join-Path $clipOut 'captions.ass'
        $maxWords = if (($layout.PSObject.Properties.Name -contains 'elements') -and ($layout.elements.PSObject.Properties.Name -contains 'caption_box')) { [int]$layout.elements.caption_box.max_words } else { [int]$layout.zones.subtitle.max_words }
        $cues = Get-DokClipCues -AllCues $allCues -ClipStart $start -ClipEnd $end -MaxWords $maxWords
        Write-DokAssFile -Cues $cues -Layout $layout -OutPath $ass

        # Render + preview.
        $okRender = Invoke-DokRenderClip -FfmpegExe $tools.Ffmpeg -BaseFrame $baseFrame -Audio $workAudio `
                     -Start $start -Duration $dur -AssFile $ass -OutMp4 $mp4 -Fps ([int]$layout.fps) -FontsDir (Join-Path $RootDir 'fonts')
        if (-not $okRender) { throw "FFmpeg produced no/empty output." }

        $preview = Join-Path $clipOut 'preview.jpg'
        Export-DokPreview -FfmpegExe $tools.Ffmpeg -Mp4 $mp4 -OutJpg $preview | Out-Null

        # QA + manifest + report.
        $qa = Test-DokClipQuality -Mp4 $mp4 -ExpectedDuration $dur -Width $cw -Height $ch -FfprobeExe $tools.Ffprobe
        $relOut = "output/$id/clip.mp4"
        $manifest = [ordered]@{
            id=$id; series=$series; episode=$episode; speaker=$speaker; headline=$clip.headline
            inputs=[ordered]@{ audio=(Split-Path $paths.Audio -Leaf); background=$(if($paths.Background){Split-Path $paths.Background -Leaf}else{'default'}); transcript=$(if($paths.Transcript){Split-Path $paths.Transcript -Leaf}else{$null}); template=$paths.Template }
            window=[ordered]@{ start=$clip.start; end=$clip.end; duration_sec=[math]::Round($dur,3) }
            render=[ordered]@{ width=$cw; height=$ch; fps=[int]$layout.fps; codec='h264/aac' }
            output=$relOut; caption_cues=$cues.Count
        }
        ($manifest | ConvertTo-Json -Depth 8) | Set-Content -LiteralPath (Join-Path $clipOut 'manifest.json') -Encoding utf8
        $report = [ordered]@{ id=$id; result='success'; qa_score=$qa.score; checks=$qa.checks; rendered_at=(Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ") }
        ($report | ConvertTo-Json -Depth 8) | Set-Content -LiteralPath (Join-Path $clipOut 'report.json') -Encoding utf8
        ($report | ConvertTo-Json -Depth 8) | Set-Content -LiteralPath (Join-Path $paths.ReportsDir "$id.json") -Encoding utf8

        Set-DokClipStatus -State $state -ClipId $id -Status 'completed' -Output $relOut
        Save-DokState -ProjectRoot $ProjectRoot -State $state
        Write-DokLog $logger "Clip $id done (QA $($qa.score))." 'OK'
        $done++
    }
    catch {
        # One clip failing must never abort the batch (spec 10, 17).
        $reason = Get-DokErrorText $_
        Write-DokLog $logger "Clip $id FAILED: $reason" 'ERROR'
        Set-DokClipStatus -State $state -ClipId $id -Status 'failed' -Reason $reason
        Save-DokState -ProjectRoot $ProjectRoot -State $state
        $failed++
    }
}

# --- 9. Final summary -------------------------------------------------------
Save-DokState -ProjectRoot $ProjectRoot -State $state
$summary = [ordered]@{
    project=$projId; total=$total; completed=$done; failed=$failed; skipped=$skipped
    finished_at=(Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}
($summary | ConvertTo-Json -Depth 4) | Set-Content -LiteralPath (Join-Path $paths.ReportsDir '_summary.json') -Encoding utf8
Write-DokLog $logger "Summary: total=$total completed=$done failed=$failed skipped=$skipped" 'STEP'
if ($failed -gt 0) { exit 1 } else { exit 0 }

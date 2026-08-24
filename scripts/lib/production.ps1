# lib/production.ps1
# Project orchestration: sanitisation, path resolution, import, the per-clip
# render loop, QA, and manifest/report writing. Delegates to the other modules.

Set-StrictMode -Version Latest

$script:DokTranscriptExt = @('.json','.srt','.vtt')
$script:DokBackgroundExt = @('.jpg','.jpeg','.png','.webp')

# --- Sanitisation (spec 19) -------------------------------------------------
function Get-DokSafeName {
    param([Parameter(Mandatory)][string]$Name)
    $invalid = [System.IO.Path]::GetInvalidFileNameChars() + [char[]]@(' ')
    $sb = New-Object System.Text.StringBuilder
    foreach ($ch in $Name.ToCharArray()) {
        if ($invalid -contains $ch) { [void]$sb.Append('_') } else { [void]$sb.Append($ch) }
    }
    $out = $sb.ToString().Trim('_','.')
    if ([string]::IsNullOrWhiteSpace($out)) { $out = 'untitled' }
    return $out
}

function Get-DokProjectId {
    param([string]$Series,[string]$Episode)
    "{0}_{1}" -f (Get-DokSafeName $Series), (Get-DokSafeName $Episode)
}

# --- Path resolution (spec 10 step 3) ---------------------------------------
function Resolve-DokPaths {
    param([Parameter(Mandatory)][string]$ProjectRoot,[Parameter(Mandatory)][string]$RootDir,[string]$Series)
    . (Join-Path $RootDir 'scripts/lib/audio.ps1')
    $root = (Resolve-Path -LiteralPath $ProjectRoot).Path

    $audio = Find-DokAudio -ProjectRoot $root

    $transcript = $null
    foreach ($e in $script:DokTranscriptExt) {
        $p = Join-Path $root "transcript$e"; if (Test-Path -LiteralPath $p) { $transcript = (Resolve-Path -LiteralPath $p).Path; break }
    }
    $projectBackground = $null
    foreach ($e in $script:DokBackgroundExt) {
        $p = Join-Path $root "background$e"; if (Test-Path -LiteralPath $p) { $projectBackground = (Resolve-Path -LiteralPath $p).Path; break }
    }
    if (-not $projectBackground -and $Series) {
        $seriesBgBase = (Get-DokSafeName $Series).ToLower()
        foreach ($e in $script:DokBackgroundExt) {
            $p = Join-Path $root "$seriesBgBase$e"
            if (Test-Path -LiteralPath $p) { $projectBackground = (Resolve-Path -LiteralPath $p).Path; break }
        }
    }

    $clipPlanPath = Join-Path $root 'clip_plan.json'
    $clipPlan = if (Test-Path -LiteralPath $clipPlanPath) { (Resolve-Path -LiteralPath $clipPlanPath).Path } else { $null }

    # Layout resolves by project-local override first, then by series template,
    # then by templates/default. That allows source-folder layouts to travel
    # with the project without manual copying.
    $tmplDir = $null
    $template = $null
    $projectLayout = Join-Path $root 'layout.json'
    if (Test-Path -LiteralPath $projectLayout) {
        $template = (Resolve-Path -LiteralPath $projectLayout).Path
        $tmplDir = $root
    } elseif ($Series) {
        $cand = Join-Path $RootDir ("templates/" + (Get-DokSafeName $Series).ToLower())
        if (Test-Path -LiteralPath (Join-Path $cand 'layout.json')) {
            $tmplDir = $cand
            $template = Join-Path $tmplDir 'layout.json'
        }
    }
    if (-not $template) {
        $def = Join-Path $RootDir 'templates/default'
        if (Test-Path -LiteralPath (Join-Path $def 'layout.json')) {
            $tmplDir = $def
            $template = Join-Path $tmplDir 'layout.json'
        }
    }

    # Prefer a project-specific background supplied in the source folder.
    # If the template opts out, its own background wins. Otherwise, the
    # project background wins over the template background, and the asset
    # fallback is used only if neither exists.
    $background = $null
    $templateBackground = $null
    $allowProjectBackground = $true
    if ($template -and (Test-Path -LiteralPath $template)) {
        try {
            $layoutObj = Get-Content -LiteralPath $template -Raw -Encoding utf8 | ConvertFrom-Json
            if (($layoutObj.PSObject.Properties.Name -contains 'allow_project_background') -and
                ($layoutObj.allow_project_background -eq $false) -and
                ($template -ne $projectLayout)) {
                $allowProjectBackground = $false
            }
            if (($layoutObj.PSObject.Properties.Name -contains 'background') -and $layoutObj.background) {
                $candBg = Join-Path $tmplDir ([string]$layoutObj.background)
                if (Test-Path -LiteralPath $candBg) { $templateBackground = (Resolve-Path -LiteralPath $candBg).Path }
            }
        } catch { }
    }
    if ($allowProjectBackground -and $projectBackground) { $background = $projectBackground }
    elseif ($templateBackground) { $background = $templateBackground }
    if (-not $background) {
        $def = Join-Path $RootDir 'assets/default_background.png'
        if (Test-Path -LiteralPath $def) { $background = (Resolve-Path -LiteralPath $def).Path }
    }

    $outDir = Join-Path $root 'output'
    $writable = $false
    try {
        if (-not (Test-Path -LiteralPath $outDir)) { New-Item -ItemType Directory -Force -Path $outDir | Out-Null }
        $probe = Join-Path $outDir '.write_test'; Set-Content -LiteralPath $probe -Value 'ok'; Remove-Item -LiteralPath $probe -Force
        $writable = $true
    } catch { $writable = $false }

    [pscustomobject]@{
        Root           = $root
        Audio          = $audio
        Transcript     = $transcript
        Background     = $background
        ClipPlan       = $clipPlan
        Template       = $(if ($template -and (Test-Path -LiteralPath $template)) { $template } else { $null })
        TemplateDir    = $tmplDir
        OutputDir      = $outDir
        LogsDir        = (Join-Path $root 'logs')
        ReportsDir     = (Join-Path $root 'reports')
        CacheDir       = (Join-Path $root 'cache')
        OutputWritable = $writable
    }
}

# --- Import wizard backend (spec 7) -----------------------------------------
function Import-DokLecture {
    param(
        [Parameter(Mandatory)][string]$RootDir,
        [Parameter(Mandatory)][string]$Series,
        [Parameter(Mandatory)][string]$Episode,
        [Parameter(Mandatory)][string]$SourceFolder
    )
    if (-not (Test-Path -LiteralPath $SourceFolder)) { throw "Source folder not found: $SourceFolder" }
    $projId = Get-DokProjectId -Series $Series -Episode $Episode
    $folderName = "{0}_{1}" -f (Get-DokSafeName $Series), (Get-DokSafeName $Episode)
    $projDir = Join-Path (Join-Path $RootDir 'Projects') $folderName
    foreach ($d in @($projDir, "$projDir/output", "$projDir/logs", "$projDir/reports", "$projDir/cache")) {
        if (-not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
    }

    # Copy recognised source files without clobbering existing project files.
    $accepted = @{}
    foreach ($e in (@('.mp3','.amr','.wav','.m4a','.m4b','.aac','.ogg','.opus','.wma','.flac','.mp4','.mov','.webm','.m4v','.mkv'))) { $accepted["audio$e"] = $true }
    foreach ($e in $script:DokTranscriptExt) { $accepted["transcript$e"] = $true }
    foreach ($e in $script:DokBackgroundExt) { $accepted["background$e"] = $true }
    $accepted['clip_plan.json'] = $true
    $accepted['layout.json'] = $true
    $seriesSafe = (Get-DokSafeName $Series).ToLower()
    $sourceSafe = (Get-DokSafeName (Split-Path -Path $SourceFolder -Leaf)).ToLower()

    $copied = New-Object System.Collections.Generic.List[string]
    foreach ($f in Get-ChildItem -LiteralPath $SourceFolder -File) {
        $lower = $f.Name.ToLower()
        $target = $null
        if ($accepted.ContainsKey($lower)) { $target = $lower }
        else {
            # Map arbitrary lecture media by extension to audio.<ext>.
            $ext = $f.Extension.ToLower()
            if ($accepted.ContainsKey("audio$ext"))      { $target = "audio$ext" }
            elseif ($accepted.ContainsKey("transcript$ext")) { $target = "transcript$ext" }
            elseif ($accepted.ContainsKey("background$ext")) { $target = "background$ext" }
            elseif (($f.BaseName.ToLower() -eq $seriesSafe) -or ($f.BaseName.ToLower() -eq $sourceSafe)) {
                if ($script:DokBackgroundExt -contains $ext) { $target = $f.Name }
            }
        }
        if ($target) {
            $dest = Join-Path $projDir $target
            if (-not (Test-Path -LiteralPath $dest)) {
                Copy-Item -LiteralPath $f.FullName -Destination $dest
                $copied.Add($target)
            }
        }
    }

    # Seed a project-local layout/background from the series template so the
    # renderer can run immediately without manual copying into the source
    # folder. The project-local files win over the shared series template.
    $templateLayoutPath = $null
    $templateBackgroundPath = $null
    $seriesTemplateDir = $null
    if ($Series) {
        $cand = Join-Path $RootDir ("templates/" + (Get-DokSafeName $Series).ToLower())
        if (Test-Path -LiteralPath (Join-Path $cand 'layout.json')) { $seriesTemplateDir = $cand }
    }
    if (-not $seriesTemplateDir) {
        $def = Join-Path $RootDir 'templates/default'
        if (Test-Path -LiteralPath (Join-Path $def 'layout.json')) { $seriesTemplateDir = $def }
    }
    if ($seriesTemplateDir) {
        $templateLayoutPath = Join-Path $seriesTemplateDir 'layout.json'
        try {
            $layoutObj = Get-Content -LiteralPath $templateLayoutPath -Raw -Encoding utf8 | ConvertFrom-Json
            $bgName = if (($layoutObj.PSObject.Properties.Name -contains 'background') -and $layoutObj.background) { [string]$layoutObj.background } else { $null }
            if ($bgName) {
                $srcBg = Join-Path $seriesTemplateDir $bgName
                if (Test-Path -LiteralPath $srcBg) { $templateBackgroundPath = $srcBg }
            }
        } catch { }
    }
    if ($templateLayoutPath -and -not (Test-Path -LiteralPath (Join-Path $projDir 'layout.json'))) {
        $seedLayout = Get-Content -LiteralPath $templateLayoutPath -Raw -Encoding utf8 | ConvertFrom-Json
        if ($templateBackgroundPath) {
            $bgExt = [System.IO.Path]::GetExtension($templateBackgroundPath)
            $bgFile = ("{0}{1}" -f ((Get-DokSafeName $Series).ToLower()), $bgExt)
            $seedLayout.background = $bgFile
            Copy-Item -LiteralPath $templateBackgroundPath -Destination (Join-Path $projDir $bgFile) -Force
        }
        ($seedLayout | ConvertTo-Json -Depth 20) | Set-Content -LiteralPath (Join-Path $projDir 'layout.json') -Encoding utf8
    }

    # Placeholder clip plan if none supplied.
    $planPath = Join-Path $projDir 'clip_plan.json'
    if (-not (Test-Path -LiteralPath $planPath)) {
        $placeholder = [ordered]@{
            series  = (Get-DokSafeName $Series).ToLower()
            episode = (Get-DokSafeName $Episode)
            speaker = ''
            clips   = @()
        }
        ($placeholder | ConvertTo-Json -Depth 6) | Set-Content -LiteralPath $planPath -Encoding utf8
    }

    # Initial render_state.json.
    $statePath = Join-Path $projDir 'render_state.json'
    if (-not (Test-Path -LiteralPath $statePath)) {
        ([ordered]@{ project = $projId; last_run = $null; clips = @() } | ConvertTo-Json -Depth 6) |
            Set-Content -LiteralPath $statePath -Encoding utf8
    }

    # Generate RUN_PROJECT.cmd with the explicit project root baked in.
    Write-DokRunProjectCmd -RootDir $RootDir -ProjectDir $projDir

    [pscustomobject]@{ ProjectId = $projId; ProjectDir = $projDir; Copied = @($copied) }
}

function Write-DokRunProjectCmd {
    param([Parameter(Mandatory)][string]$RootDir,[Parameter(Mandatory)][string]$ProjectDir)
    $cmd = @'
@echo off
REM Auto-generated project launcher. Passes the project root explicitly.
setlocal
set "PROJECT_ROOT=%~dp0"
if "%PROJECT_ROOT:~-1%"=="\" set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"
set "DOK_ROOT=__DOK_ROOT__"
powershell -NoProfile -ExecutionPolicy Bypass -File "%DOK_ROOT%\scripts\render_project.ps1" -ProjectRoot "%PROJECT_ROOT%" -RootDir "%DOK_ROOT%"
echo.
pause
endlocal
'@
    $cmd = $cmd.Replace('__DOK_ROOT__', $RootDir)
    Set-Content -LiteralPath (Join-Path $ProjectDir 'RUN_PROJECT.cmd') -Value $cmd -Encoding ascii
}

# --- QA (spec 15) -----------------------------------------------------------
function Test-DokClipQuality {
    param([string]$Mp4,[double]$ExpectedDuration,[int]$Width,[int]$Height,[string]$FfprobeExe)

    # Keep this deliberately simple and PowerShell-5.1-safe. Earlier versions
    # used Generic.List + [pscustomobject] nesting, which can throw
    # "Argument types do not match" after a clip has already rendered.
    $checks = @()

    $fileOk = (Test-Path -LiteralPath $Mp4) -and ((Get-Item -LiteralPath $Mp4).Length -gt 1024)
    $checks += [ordered]@{ check = 'file_exists'; pass = [bool]$fileOk }

    if ($fileOk -and $FfprobeExe) {
        try {
            $old = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            try {
                $info = & $FfprobeExe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 $Mp4 2>$null
                $videoCode = $LASTEXITCODE
                if ($videoCode -eq 0 -and $info) {
                    $parts = (($info | Select-Object -First 1) -split ',')
                    if ($parts.Count -ge 2) {
                        $w = [int]$parts[0]
                        $h = [int]$parts[1]
                        $expectedW = $Width + ($Width % 2)
                        $expectedH = $Height + ($Height % 2)
                        $checks += [ordered]@{ check = 'resolution'; pass = [bool]($w -eq $expectedW -and $h -eq $expectedH); detail = "${w}x${h}" }
                    }
                }

                $aud = & $FfprobeExe -v error -select_streams a:0 -show_entries stream=codec_type -of csv=p=0 $Mp4 2>$null
                $audioCode = $LASTEXITCODE
                $checks += [ordered]@{ check = 'has_audio'; pass = [bool]($audioCode -eq 0 -and $aud) }
            }
            finally {
                $ErrorActionPreference = $old
            }
        } catch {
            # QA must never fail a render. If probing fails, record a warning check.
            $checks += [ordered]@{ check = 'ffprobe_probe'; pass = $false; detail = 'ffprobe could not inspect output' }
        }
    }

    $passed = 0
    foreach ($c in @($checks)) {
        if ($c.Contains('pass') -and [bool]$c['pass']) { $passed++ }
    }
    $score = if (@($checks).Count -gt 0) { [int][math]::Round(($passed / @($checks).Count) * 100) } else { 0 }

    return [pscustomobject]([ordered]@{
        score  = [int]$score
        checks = [object[]]@($checks)
    })
}

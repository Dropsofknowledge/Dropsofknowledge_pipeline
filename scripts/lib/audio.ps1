# lib/audio.ps1
# Audio resolution + conversion (spec 10 step 4). Many container/codec inputs
# are accepted; we normalise to a working AAC/m4a if needed and probe duration.

Set-StrictMode -Version Latest

$script:DokAudioExt = @('.mp3','.amr','.wav','.m4a','.m4b','.aac','.ogg','.opus','.wma','.flac','.mp4','.mov','.webm','.m4v','.mkv')

function Find-DokAudio {
    param([Parameter(Mandatory)][string]$ProjectRoot)
    foreach ($ext in $script:DokAudioExt) {
        $p = Join-Path $ProjectRoot "audio$ext"
        if (Test-Path -LiteralPath $p) { return (Resolve-Path -LiteralPath $p).Path }
    }
    $cand = Get-ChildItem -LiteralPath $ProjectRoot -Filter 'audio.*' -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cand) { return $cand.FullName }
    return $null
}

# Native tools such as FFmpeg write normal progress/version text to STDERR.
# With $ErrorActionPreference='Stop', Windows PowerShell can turn that text into
# a NativeCommandError. Run native commands with EAP temporarily relaxed.
function Invoke-DokNativeQuiet {
    param(
        [Parameter(Mandatory)][string]$Exe,
        [Parameter(Mandatory)][string[]]$Args,
        [switch]$CaptureStdout
    )
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if ($CaptureStdout) {
            $out = & $Exe @Args 2>$null
            $code = $LASTEXITCODE
            return [pscustomobject]@{ ExitCode = $code; Output = $out }
        } else {
            & $Exe @Args 2>&1 | Out-Null
            $code = $LASTEXITCODE
            return [pscustomobject]@{ ExitCode = $code; Output = $null }
        }
    }
    finally {
        $ErrorActionPreference = $old
    }
}

function Get-DokAudioDuration {
    param([Parameter(Mandatory)][string]$FfprobeExe,[Parameter(Mandatory)][string]$Path)
    try {
        $res = Invoke-DokNativeQuiet -Exe $FfprobeExe -Args @('-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',$Path) -CaptureStdout
        if ($res.ExitCode -eq 0 -and $res.Output) { return [double]($res.Output | Select-Object -First 1) }
    } catch { }
    return 0
}

# Ensure we have an AAC m4a in cache/. Returns path to use for muxing.
function Resolve-DokWorkingAudio {
    param(
        [Parameter(Mandatory)][string]$FfmpegExe,
        [Parameter(Mandatory)][string]$SourceAudio,
        [Parameter(Mandatory)][string]$CacheDir
    )
    $ext = [System.IO.Path]::GetExtension($SourceAudio).ToLower()
    if ($ext -eq '.m4a' -or $ext -eq '.aac' -or $ext -eq '.mp3') {
        return $SourceAudio
    }
    if (-not (Test-Path -LiteralPath $CacheDir)) { New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null }
    $work = Join-Path $CacheDir 'audio_work.m4a'
    if (-not (Test-Path -LiteralPath $work)) {
        $args = @('-y','-hide_banner','-loglevel','error','-i',$SourceAudio,'-vn','-c:a','aac','-b:a','192k',$work)
        $res = Invoke-DokNativeQuiet -Exe $FfmpegExe -Args $args
        if ($res.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $work)) {
            throw "FFmpeg failed to convert source audio to working AAC file: $SourceAudio"
        }
    }
    if (Test-Path -LiteralPath $work) { return $work }
    return $SourceAudio
}

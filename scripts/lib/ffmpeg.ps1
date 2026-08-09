# lib/ffmpeg.ps1
# FFmpeg / ImageMagick wrappers.

Set-StrictMode -Version Latest

function Resolve-DokTool {
    param([Parameter(Mandatory)][string]$Name,[string[]]$Candidates,[string[]]$FallbackNames = @())
    foreach ($c in $Candidates) {
        if ($c -and (Test-Path -LiteralPath $c)) { return (Resolve-Path -LiteralPath $c).Path }
    }
    foreach ($n in @($Name) + $FallbackNames) {
        $cmd = Get-Command $n -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    throw "$Name not found. Install it or set its path in scripts/config.ps1."
}

function Get-DokTools {
    param([string]$RootDir)
    $cfg = Join-Path $RootDir 'scripts/config.ps1'
    $DokFfmpeg = $null; $DokFfprobe = $null; $DokMagick = $null
    if (Test-Path -LiteralPath $cfg) { . $cfg }
    [pscustomobject]@{
        Ffmpeg  = Resolve-DokTool -Name 'ffmpeg'  -Candidates @($DokFfmpeg)
        Ffprobe = Resolve-DokTool -Name 'ffprobe' -Candidates @($DokFfprobe)
        Magick  = Resolve-DokTool -Name 'magick'  -Candidates @($DokMagick) -FallbackNames @('convert')
    }
}

# Native tools write normal progress/version text to STDERR. With
# $ErrorActionPreference='Stop', Windows PowerShell can turn that into a thrown
# NativeCommandError. This helper suppresses/captures native output safely.
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

function Convert-DokSvgToPng {
    param(
        [Parameter(Mandatory)][string]$MagickExe,
        [Parameter(Mandatory)][string]$Svg,
        [Parameter(Mandatory)][string]$OutPng,
        [int]$Width,[int]$Height
    )
    $tmpSvg = "$OutPng.svg"
    Set-Content -LiteralPath $tmpSvg -Value $Svg -Encoding utf8
    $res = Invoke-DokNativeQuiet -Exe $MagickExe -Args @('-background','none','-size',"${Width}x${Height}",$tmpSvg,$OutPng)
    Remove-Item -LiteralPath $tmpSvg -ErrorAction SilentlyContinue
    return ($res.ExitCode -eq 0) -and (Test-Path -LiteralPath $OutPng)
}

function New-DokBaseFrame {
    param(
        [Parameter(Mandatory)][string]$MagickExe,
        [string]$Background,
        [Parameter(Mandatory)][string]$OverlayPng,
        [Parameter(Mandatory)][string]$OutPng,
        [int]$Width,[int]$Height,[string]$BgColor = '#0B0B0F'
    )
    if ($Background -and (Test-Path -LiteralPath $Background)) {
        $args = @($Background,'-resize',"${Width}x${Height}^",'-gravity','center','-extent',"${Width}x${Height}",$OverlayPng,'-gravity','center','-composite',$OutPng)
    } else {
        $args = @('-size',"${Width}x${Height}","xc:$BgColor",$OverlayPng,'-gravity','center','-composite',$OutPng)
    }
    $res = Invoke-DokNativeQuiet -Exe $MagickExe -Args $args
    return ($res.ExitCode -eq 0) -and (Test-Path -LiteralPath $OutPng)
}

function ConvertTo-DokFfmpegFilterPath {
    param([string]$Path)
    return ($Path -replace '\\','/' -replace ':','\:')
}

function Invoke-DokRenderClip {
    param(
        [Parameter(Mandatory)][string]$FfmpegExe,
        [Parameter(Mandatory)][string]$BaseFrame,
        [Parameter(Mandatory)][string]$Audio,
        [Parameter(Mandatory)][double]$Start,
        [Parameter(Mandatory)][double]$Duration,
        [string]$AssFile,
        [Parameter(Mandatory)][string]$OutMp4,
        [int]$Fps = 30,
        [string]$FontsDir
    )
    $even = 'pad=ceil(iw/2)*2:ceil(ih/2)*2'
    $vf = "$even,format=yuv420p"
    if ($AssFile -and (Test-Path -LiteralPath $AssFile)) {
        $esc = ConvertTo-DokFfmpegFilterPath $AssFile
        if ($FontsDir -and (Test-Path -LiteralPath $FontsDir)) {
            $fd = ConvertTo-DokFfmpegFilterPath $FontsDir
            $vf = "ass='$esc':fontsdir='$fd',$even,format=yuv420p"
        } else {
            $vf = "ass='$esc',$even,format=yuv420p"
        }
    }
    $args = @(
        '-y','-hide_banner','-loglevel','error',
        '-loop','1','-framerate',"$Fps",'-i', $BaseFrame,
        '-ss', ("{0:0.###}" -f $Start), '-t', ("{0:0.###}" -f $Duration), '-i', $Audio,
        '-vf', $vf,
        '-c:v','libx264','-pix_fmt','yuv420p','-preset','veryfast','-crf','20',
        '-c:a','aac','-b:a','192k',
        '-r', "$Fps",
        '-t', ("{0:0.###}" -f $Duration),
        '-shortest', '-movflags','+faststart',
        $OutMp4
    )
    $res = Invoke-DokNativeQuiet -Exe $FfmpegExe -Args $args
    $ok = ($res.ExitCode -eq 0) -and (Test-Path -LiteralPath $OutMp4) -and ((Get-Item -LiteralPath $OutMp4).Length -gt 1024)
    if (-not $ok -and (Test-Path -LiteralPath $OutMp4)) { Remove-Item -LiteralPath $OutMp4 -Force -ErrorAction SilentlyContinue }
    return $ok
}

function Export-DokPreview {
    param([Parameter(Mandatory)][string]$FfmpegExe,[string]$Mp4,[string]$OutJpg)
    $res = Invoke-DokNativeQuiet -Exe $FfmpegExe -Args @('-y','-hide_banner','-loglevel','error','-ss','0.5','-i',$Mp4,'-frames:v','1','-q:v','3',$OutJpg)
    return ($res.ExitCode -eq 0) -and (Test-Path -LiteralPath $OutJpg)
}

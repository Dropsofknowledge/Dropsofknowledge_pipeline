# lib/captions.ps1
# Caption handling (spec 14). Parses transcript (json/srt/vtt), slices it to a
# clip window, chunks it into short readable cues, and writes an ASS subtitle
# file. Supports both the original bottom-subtitle layout and the newer Kabair
# poster layout where captions sit inside the built-in white caption box.

Set-StrictMode -Version Latest

function ConvertTo-DokTimeFromSrt {
    param([string]$T)
    $T = $T.Trim().Replace(',', '.')
    $parts = $T.Split(':')
    if ($parts.Count -ne 3) { return $null }
    [double]$h = $parts[0]; [double]$m = $parts[1]; [double]$s = $parts[2]
    $h * 3600 + $m * 60 + $s
}

function Read-DokTranscript {
    param([string]$Path)
    $cues = New-Object System.Collections.Generic.List[object]
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $cues }
    $ext = [System.IO.Path]::GetExtension($Path).ToLower()
    $raw = Get-Content -LiteralPath $Path -Raw -Encoding utf8

    if ($ext -eq '.json') {
        try {
            $obj = $raw | ConvertFrom-Json
            $segs = if ($obj.PSObject.Properties.Name -contains 'segments') { $obj.segments } else { $obj }
            foreach ($s in @($segs)) {
                $cues.Add([pscustomobject]@{ start = [double]$s.start; end = [double]$s.end; text = [string]$s.text })
            }
        } catch { }
        return $cues
    }

    $text = $raw -replace "`r",""
    $blocks = $text -split "`n`n+"
    foreach ($b in $blocks) {
        $lines = @($b -split "`n" | Where-Object { $_ -ne '' })
        $timing = $lines | Where-Object { $_ -match '-->' } | Select-Object -First 1
        if (-not $timing) { continue }
        $m = [regex]::Match($timing, '([0-9:.,]+)\s*-->\s*([0-9:.,]+)')
        if (-not $m.Success) { continue }
        $st = ConvertTo-DokTimeFromSrt $m.Groups[1].Value
        $en = ConvertTo-DokTimeFromSrt $m.Groups[2].Value
        $idx = [array]::IndexOf($lines, $timing)
        if ($idx -lt 0 -or $idx -ge ($lines.Count - 1)) { continue }
        $txt = ($lines[($idx+1)..($lines.Count-1)] -join ' ').Trim()
        if ($null -ne $st -and $null -ne $en -and $txt) {
            $cues.Add([pscustomobject]@{ start = $st; end = $en; text = $txt })
        }
    }
    return $cues
}

function Get-DokClipCues {
    param(
        [Parameter(Mandatory)]$AllCues,
        [Parameter(Mandatory)][double]$ClipStart,
        [Parameter(Mandatory)][double]$ClipEnd,
        [int]$MaxWords = 7
    )
    $out = New-Object System.Collections.Generic.List[object]
    foreach ($c in $AllCues) {
        if ($c.end -le $ClipStart -or $c.start -ge $ClipEnd) { continue }
        $s = [math]::Max($c.start, $ClipStart) - $ClipStart
        $e = [math]::Min($c.end, $ClipEnd) - $ClipStart
        $dur = $e - $s
        if ($dur -le 0) { continue }
        $words = @($c.text -split '\s+' | Where-Object { $_ -ne '' })
        if ($words.Count -eq 0) { continue }
        $chunks = New-Object System.Collections.Generic.List[string]
        $cur = New-Object System.Collections.Generic.List[string]
        foreach ($w in $words) {
            $cur.Add($w)
            $endsPunct = $w -match '[.!?,;:]$'
            if ($cur.Count -ge $MaxWords -or ($endsPunct -and $cur.Count -ge [int]($MaxWords/2))) {
                $chunks.Add(($cur -join ' ')); $cur.Clear()
            }
        }
        if ($cur.Count -gt 0) { $chunks.Add(($cur -join ' ')) }
        $per = $dur / [math]::Max(1, $chunks.Count)
        for ($i = 0; $i -lt $chunks.Count; $i++) {
            $out.Add([pscustomobject]@{ start = $s + $i * $per; end = $s + ($i + 1) * $per; text = $chunks[$i] })
        }
    }
    return $out
}

function ConvertTo-DokAssTime {
    param([double]$Sec)
    if ($Sec -lt 0) { $Sec = 0 }
    $h = [int]([math]::Floor($Sec / 3600))
    $m = [int]([math]::Floor(($Sec % 3600) / 60))
    $s = $Sec % 60
    "{0}:{1:00}:{2:00.00}" -f $h, $m, $s
}

function ConvertTo-DokAssColor {
    param([string]$Hex)
    $s = $Hex.TrimStart('#')
    if ($s.Length -lt 6) { return '&H00000000' }
    $r = $s.Substring(0,2); $g = $s.Substring(2,2); $b = $s.Substring(4,2)
    return "&H00$b$g$r".ToUpperInvariant()
}

function Get-DokAssFontName {
    param([string]$Font)
    $base = ([System.IO.Path]::GetFileNameWithoutExtension($Font)).ToLowerInvariant()
    if ($base -match 'pt.?serif') { return 'PT Serif' }
    if ($base -match 'montserrat') { return 'Montserrat' }
    if ($base -match 'oswald') { return 'Oswald' }
    if ($base -match 'noto.*arabic') { return 'Noto Naskh Arabic' }
    return $Font
}

function Split-DokAssCaptionLines {
    param([string]$Text,[int]$MaxChars = 34,[int]$MaxLines = 2)
    $words = @($Text -split '\s+' | Where-Object { $_ -ne '' })
    $lines = New-Object System.Collections.Generic.List[string]
    $cur = ''
    foreach ($w in $words) {
        $cand = if ($cur -eq '') { $w } else { "$cur $w" }
        if ($cand.Length -le $MaxChars -or $cur -eq '') { $cur = $cand }
        else { $lines.Add($cur); $cur = $w }
    }
    if ($cur) { $lines.Add($cur) }
    if ($MaxLines -gt 0 -and $lines.Count -gt $MaxLines) { return @($lines.ToArray() | Select-Object -First $MaxLines) }
    return @($lines.ToArray())
}

function Write-DokAssFile {
    param(
        [Parameter(Mandatory)]$Cues,
        [Parameter(Mandatory)]$Layout,
        [Parameter(Mandatory)][string]$OutPath
    )
    $w = [int]$Layout.canvas.width
    $h = [int]$Layout.canvas.height

    # New poster-style caption box.
    if (($Layout.PSObject.Properties.Name -contains 'elements') -and ($Layout.elements.PSObject.Properties.Name -contains 'caption_box')) {
        $box = $Layout.elements.caption_box
        $fontRaw = if ($Layout.fonts.PSObject.Properties.Name -contains 'subtitle') { [string]$Layout.fonts.subtitle } else { 'PT Serif' }
        $font = Get-DokAssFontName $fontRaw
        $size = [int]$box.font_size
        $color = ConvertTo-DokAssColor ([string]$box.color)
        $cx = [int]([double]$box.x + [double]$box.width / 2)
        $cy = [int]([double]$box.y + [double]$box.height / 2)
        $maxChars = if ($box.PSObject.Properties.Name -contains 'max_chars_per_line') { [int]$box.max_chars_per_line } else { 30 }
        $maxLines = if ($box.PSObject.Properties.Name -contains 'max_lines') { [int]$box.max_lines } else { 2 }
        $header = @"
[Script Info]
ScriptType: v4.00+
PlayResX: $w
PlayResY: $h
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,$font,$size,$color,$color,&H00FFFFFF,1,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"@
        $sb = New-Object System.Text.StringBuilder
        [void]$sb.AppendLine($header)
        foreach ($c in $Cues) {
            $st = ConvertTo-DokAssTime $c.start
            $en = ConvertTo-DokAssTime $c.end
            $txt = ([string]$c.text).Replace("`n", ' ').Replace('{','(').Replace('}',')')
            $lines = @(Split-DokAssCaptionLines -Text $txt -MaxChars $maxChars -MaxLines $maxLines)
            $body = ($lines -join '\N')
            [void]$sb.AppendLine("Dialogue: 0,$st,$en,Caption,,0,0,0,,{\pos($cx,$cy)}$body")
        }
        Set-Content -LiteralPath $OutPath -Value $sb.ToString() -Encoding utf8
        return
    }

    # Legacy bottom subtitle layout.
    $sub = $Layout.zones.subtitle
    $font = $Layout.fonts.subtitle
    $size = [int]$sub.font_size
    $marginV = [int]($h - $sub.y - $sub.height)
    $marginL = [int]$sub.margin_x
    $marginR = [int]$sub.margin_x
    $header = @"
[Script Info]
ScriptType: v4.00+
PlayResX: $w
PlayResY: $h
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,$font,$size,&H00FFFFFF,&H00000000,&H64000000,1,1,$([int]$sub.outline),$([int]$sub.shadow),2,$marginL,$marginR,$marginV,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"@
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine($header)
    foreach ($c in $Cues) {
        $st = ConvertTo-DokAssTime $c.start
        $en = ConvertTo-DokAssTime $c.end
        $txt = ([string]$c.text).Replace("`n", ' ').Replace('{','(').Replace('}',')')
        [void]$sb.AppendLine("Dialogue: 0,$st,$en,Caption,,0,0,0,,$txt")
    }
    Set-Content -LiteralPath $OutPath -Value $sb.ToString() -Encoding utf8
}

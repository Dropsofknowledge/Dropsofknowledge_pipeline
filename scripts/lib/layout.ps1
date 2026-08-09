# lib/layout.ps1
# Template-driven layout (spec 12-13).
# Supports the original generic layout format and the newer poster-style
# element layout used by the Kabair template.

Set-StrictMode -Version Latest

function Get-DokLayout {
    param([Parameter(Mandatory)][string]$LayoutPath)
    if (-not (Test-Path -LiteralPath $LayoutPath)) { throw "Layout not found: $LayoutPath" }
    Get-Content -LiteralPath $LayoutPath -Raw -Encoding utf8 | ConvertFrom-Json
}

function ConvertTo-DokXmlText {
    param([string]$Text)
    if ($null -eq $Text) { return '' }
    $Text.Replace('&','&amp;').Replace('<','&lt;').Replace('>','&gt;').Replace('"','&quot;')
}

function Test-DokArabicChar {
    param([char]$Ch)
    $o = [int][char]$Ch
    return (($o -ge 0x0600 -and $o -le 0x06FF) -or ($o -ge 0x0750 -and $o -le 0x077F) -or
            ($o -ge 0x08A0 -and $o -le 0x08FF) -or ($o -ge 0xFB50 -and $o -le 0xFDFF) -or
            ($o -ge 0xFE70 -and $o -le 0xFEFF))
}

function Resolve-DokLayoutFont {
    param([string]$Font,[string]$RootDir)
    if (-not $Font) { return 'Arial' }
    if ([System.IO.Path]::IsPathRooted($Font) -and (Test-Path -LiteralPath $Font)) { return (Resolve-Path -LiteralPath $Font).Path }
    if ($RootDir) {
        $cand = Join-Path $RootDir $Font
        if (Test-Path -LiteralPath $cand) { return (Resolve-Path -LiteralPath $cand).Path }
    }
    return $Font
}

function ConvertTo-DokFileUri {
    param([string]$Path)
    if (-not $Path) { return '' }
    if (-not ([System.IO.Path]::IsPathRooted($Path)) -or -not (Test-Path -LiteralPath $Path)) { return '' }
    $full = (Resolve-Path -LiteralPath $Path).Path
    $uri = ([System.Uri]$full).AbsoluteUri
    return $uri
}

function Add-DokSvgFontFace {
    param([System.Text.StringBuilder]$Sb,[string]$Alias,[string]$FontPath,[string]$Weight='400')
    $uri = ConvertTo-DokFileUri $FontPath
    if ($uri) {
        [void]$Sb.AppendLine("@font-face { font-family: '$Alias'; src: url('$uri'); font-weight: $Weight; }")
    }
}

function ConvertTo-DokSvgRichText {
    param([string]$Text,[string]$BaseFont,[string]$ArabicFont)
    if ($null -eq $Text) { return '' }
    $sb = New-Object System.Text.StringBuilder
    foreach ($ch in $Text.ToCharArray()) {
        $enc = ConvertTo-DokXmlText ([string]$ch)
        if ($ArabicFont -and (Test-DokArabicChar $ch)) {
            [void]$sb.Append("<tspan font-family='$ArabicFont'>$enc</tspan>")
        } else {
            [void]$sb.Append($enc)
        }
    }
    return $sb.ToString()
}

function Get-DokTextWidthEstimate {
    param([string]$Text,[double]$Size,[double]$CharWidthRatio = 0.52)
    if ([string]::IsNullOrEmpty($Text)) { return 0 }
    # Latin serif uppercase/lowercase average. Spaces are narrower; Arabic marks are wider.
    [double]$w = 0
    foreach ($ch in $Text.ToCharArray()) {
        if ($ch -eq ' ') { $w += $Size * 0.28 }
        elseif (Test-DokArabicChar $ch) { $w += $Size * 0.78 }
        elseif ($ch -match '[ilI\.,;:\|]') { $w += $Size * 0.28 }
        elseif ($ch -match '[MW@#]') { $w += $Size * 0.82 }
        else { $w += $Size * $CharWidthRatio }
    }
    return $w
}

function Split-DokTextLines {
    param([string]$Text,[int]$MaxCharsPerLine = 26)
    $words = ($Text -split '\s+') | Where-Object { $_ -ne '' }
    $lines = New-Object System.Collections.Generic.List[string]
    $cur = ''
    foreach ($w in $words) {
        $cand = if ($cur -eq '') { $w } else { "$cur $w" }
        if ($cand.Length -le $MaxCharsPerLine -or $cur -eq '') { $cur = $cand }
        else { $lines.Add($cur); $cur = $w }
    }
    if ($cur -ne '') { $lines.Add($cur) }
    if ($lines.Count -eq 0) { $lines.Add('') }
    return @($lines.ToArray())
}

function Get-DokFitBlock {
    param([string]$Text,$Element,[string]$Font,[int]$DefaultMax = 80,[int]$DefaultMin = 30)
    $max = if ($Element.PSObject.Properties.Name -contains 'font_size') { [int]$Element.font_size } elseif ($Element.PSObject.Properties.Name -contains 'font_max') { [int]$Element.font_max } else { $DefaultMax }
    $min = if ($Element.PSObject.Properties.Name -contains 'font_min') { [int]$Element.font_min } else { $DefaultMin }
    $maxLines = if ($Element.PSObject.Properties.Name -contains 'max_lines') { [int]$Element.max_lines } else { 99 }
    $maxChars = if ($Element.PSObject.Properties.Name -contains 'max_chars_per_line') { [int]$Element.max_chars_per_line } else { 26 }
    $ratio = if ($Element.PSObject.Properties.Name -contains 'char_width_ratio') { [double]$Element.char_width_ratio } else { 0.52 }
    for ($size = $max; $size -ge $min; $size -= 2) {
        $lines = @(Split-DokTextLines -Text $Text -MaxCharsPerLine $maxChars)
        if ($lines.Count -le $maxLines) {
            $ok = $true
            foreach ($ln in $lines) {
                if ((Get-DokTextWidthEstimate -Text $ln -Size $size -CharWidthRatio $ratio) -gt [double]$Element.width) { $ok = $false; break }
            }
            if ($ok) { return [pscustomobject]@{ size=$size; lines=$lines } }
        }
        # If too many lines, increase chars per line slightly as size shrinks.
        if ($size -lt ($max * 0.8)) { $maxChars = [math]::Min($maxChars + 1, 80) }
    }
    [pscustomobject]@{ size=$min; lines=@(Split-DokTextLines -Text $Text -MaxCharsPerLine $maxChars) }
}

function Add-DokSvgTextBlock {
    param(
        [Parameter(Mandatory)][System.Text.StringBuilder]$Sb,
        [Parameter(Mandatory)]$Element,
        [string]$Text,
        [string]$Font,
        [string]$ArabicFont
    )
    if ([string]::IsNullOrWhiteSpace($Text)) { return }
    if (($Element.PSObject.Properties.Name -contains 'uppercase') -and $Element.uppercase) { $Text = $Text.ToUpperInvariant() }
    $fit = Get-DokFitBlock -Text $Text -Element $Element -Font $Font
    $size = [double]$fit.size
    $lineSpacing = if ($Element.PSObject.Properties.Name -contains 'line_spacing') { [double]$Element.line_spacing } else { 1.15 }
    $lineH = $size * $lineSpacing
    $lines = @($fit.lines)
    $blockH = $lineH * $lines.Count
    $valign = if ($Element.PSObject.Properties.Name -contains 'valign') { [string]$Element.valign } else { 'top' }
    if ($valign -eq 'middle') { $y = [double]$Element.y + (([double]$Element.height - $blockH) / 2) + $size }
    elseif ($valign -eq 'bottom') { $y = [double]$Element.y + [double]$Element.height - $blockH + $size }
    else { $y = [double]$Element.y + $size }
    $align = if ($Element.PSObject.Properties.Name -contains 'align') { [string]$Element.align } else { 'center' }
    $anchor = switch ($align) { 'left' { 'start' } 'right' { 'end' } default { 'middle' } }
    $x = switch ($align) { 'left' { [double]$Element.x } 'right' { [double]$Element.x + [double]$Element.width } default { [double]$Element.x + ([double]$Element.width / 2) } }
    $color = if ($Element.PSObject.Properties.Name -contains 'color') { [string]$Element.color } else { '#FFFFFF' }
    $weight = if ($Element.PSObject.Properties.Name -contains 'font_weight') { [string]$Element.font_weight } else { '700' }
    $letterSpacing = if ($Element.PSObject.Properties.Name -contains 'letter_spacing') { [double]$Element.letter_spacing } else { 0 }
    $outline = if ($Element.PSObject.Properties.Name -contains 'outline') { [double]$Element.outline } else { 0 }
    $outlineColor = if ($Element.PSObject.Properties.Name -contains 'outline_color') { [string]$Element.outline_color } else { '#000000' }
    foreach ($ln in $lines) {
        $rich = ConvertTo-DokSvgRichText -Text $ln -BaseFont $Font -ArabicFont $ArabicFont
        $strokeAttrs = if ($outline -gt 0) { " stroke='$outlineColor' stroke-width='$outline' paint-order='stroke fill'" } else { '' }
        $lsAttr = if ($letterSpacing -ne 0) { " letter-spacing='$letterSpacing'" } else { '' }
        [void]$Sb.AppendLine("<text x='$x' y='$y' font-family='$Font' font-size='$size' font-weight='$weight' text-anchor='$anchor' fill='$color'$strokeAttrs$lsAttr>$rich</text>")
        $y += $lineH
    }
}

# Build an SVG string for the static overlay.
function New-DokOverlaySvg {
    param(
        [Parameter(Mandatory)]$Layout,
        [string]$Headline = '',
        [string]$Speaker  = '',
        [string]$Series   = '',
        [string]$ClipId   = '',
        [string]$RootDir  = ''
    )
    $w = [int]$Layout.canvas.width
    $h = [int]$Layout.canvas.height
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine("<svg xmlns='http://www.w3.org/2000/svg' width='$w' height='$h' viewBox='0 0 $w $h'>")

    # New poster-style element layout.
    if ($Layout.PSObject.Properties.Name -contains 'elements') {
        $fonts = $Layout.fonts
        $idFontPath      = Resolve-DokLayoutFont -Font $fonts.id       -RootDir $RootDir
        $titleFontPath   = Resolve-DokLayoutFont -Font $fonts.title    -RootDir $RootDir
        $speakerFontPath = Resolve-DokLayoutFont -Font $fonts.speaker  -RootDir $RootDir
        $arabicFontPath  = if ($fonts.PSObject.Properties.Name -contains 'arabic') { Resolve-DokLayoutFont -Font $fonts.arabic -RootDir $RootDir } else { $null }

        # Use @font-face aliases. This is much more reliable on Windows than
        # using a .ttf path directly as font-family, and avoids fallback to Arial.
        [void]$sb.AppendLine('<defs><style type="text/css"><![CDATA[')
        Add-DokSvgFontFace -Sb $sb -Alias 'DokIdFont'      -FontPath $idFontPath      -Weight '700'
        Add-DokSvgFontFace -Sb $sb -Alias 'DokTitleFont'   -FontPath $titleFontPath   -Weight '400'
        Add-DokSvgFontFace -Sb $sb -Alias 'DokSpeakerFont' -FontPath $speakerFontPath -Weight '700'
        if ($arabicFontPath) { Add-DokSvgFontFace -Sb $sb -Alias 'DokArabicFont' -FontPath $arabicFontPath -Weight '400' }
        [void]$sb.AppendLine(']]></style></defs>')

        $idFont = 'DokIdFont'
        $titleFont = 'DokTitleFont'
        $speakerFont = 'DokSpeakerFont'
        $arabicFont = if ($arabicFontPath) { 'DokArabicFont' } else { $null }
        $els = $Layout.elements
        if (($els.PSObject.Properties.Name -contains 'id') -and $els.id.enabled -and $ClipId) {
            $idText = "$($els.id.prefix)$ClipId"
            Add-DokSvgTextBlock -Sb $sb -Element $els.id -Text $idText -Font $idFont -ArabicFont $arabicFont
        }
        if (($els.PSObject.Properties.Name -contains 'title') -and $els.title.enabled) {
            Add-DokSvgTextBlock -Sb $sb -Element $els.title -Text $Headline -Font $titleFont -ArabicFont $arabicFont
        }
        if (($els.PSObject.Properties.Name -contains 'speaker') -and $els.speaker.enabled) {
            Add-DokSvgTextBlock -Sb $sb -Element $els.speaker -Text $Speaker -Font $speakerFont -ArabicFont $arabicFont
        }
        [void]$sb.AppendLine('</svg>')
        return $sb.ToString()
    }

    # Legacy generic layout fallback.
    $tz = $Layout.zones.title
    $sz = $Layout.zones.speaker
    $titleFont   = $Layout.fonts.title
    $speakerFont = $Layout.fonts.speaker
    $titleLines = @(Split-DokTextLines -Text $Headline -MaxCharsPerLine ([int]$tz.max_chars_per_line))
    $fontSize = 80
    if ($tz.PSObject.Properties.Name -contains 'font_max') { $fontSize = [int]$tz.font_max }
    $fit = Get-DokFitBlock -Text $Headline -Element $tz -Font $titleFont
    $fontSize = $fit.size
    if ($tz.scrim) { [void]$sb.AppendLine("<rect x='0' y='$([int]$tz.y - 40)' width='$w' height='$([int]$tz.height + 80)' fill='black' fill-opacity='0.45'/>") }
    Add-DokSvgTextBlock -Sb $sb -Element $tz -Text $Headline -Font $titleFont -ArabicFont $null
    if (-not [string]::IsNullOrWhiteSpace($Speaker)) { Add-DokSvgTextBlock -Sb $sb -Element $sz -Text $Speaker -Font $speakerFont -ArabicFont $null }
    if ($Layout.branding.show -and -not [string]::IsNullOrWhiteSpace($Series)) {
        $bt = ConvertTo-DokXmlText $Series.ToUpper()
        [void]$sb.AppendLine("<text x='$([int]($w/2))' y='$([int]$Layout.branding.y)' font-family='$titleFont' font-size='$([int]$Layout.branding.font)' font-weight='700' letter-spacing='4' text-anchor='middle' fill='$($Layout.branding.color)' fill-opacity='0.85'>$bt</text>")
    }
    [void]$sb.AppendLine('</svg>')
    return $sb.ToString()
}

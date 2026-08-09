# scripts/config.ps1
# Optional explicit tool paths. Leave blank to auto-discover from PATH.
# Example:
#   $DokFfmpeg  = 'C:\ffmpeg\bin\ffmpeg.exe'
#   $DokFfprobe = 'C:\ffmpeg\bin\ffprobe.exe'
#   $DokMagick  = 'C:\Program Files\ImageMagick\magick.exe'

$DokFfmpeg  = $env:DOK_FFMPEG
$DokFfprobe = $env:DOK_FFPROBE
$DokMagick  = $env:DOK_MAGICK

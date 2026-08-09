# Fonts used by the Kabair template

| Role in design        | Requested font        | Shipped font (free/embeddable)        | File                      |
|-----------------------|-----------------------|----------------------------------------|---------------------------|
| Headline              | PT Serif              | PT Serif (exact)                       | PTSerif-Regular/Bold.ttf  |
| Sheikh name           | Galacial Indifference | Montserrat SemiBold (close substitute) | Montserrat-SemiBold.ttf   |
| Series ID number      | Kingred Modern        | Oswald (condensed display substitute)  | Oswald.ttf                |
| Caption box text      | (inferred bold serif) | PT Serif Bold                          | PTSerif-Bold.ttf          |
| Arabic glyph fallback | needed for ﷺ/Arabic   | Noto Naskh Arabic                      | NotoNaskhArabic-Regular.ttf |

Glacial Indifference and Kingred Modern are not freely redistributable here,
so close free substitutes are shipped. To use the exact fonts, drop their .ttf
into this folder and update `templates/kabair/layout.json` `fonts` block.

The PowerShell renderer writes font file paths into the SVG overlay and passes
`fonts/` to FFmpeg's ASS subtitle filter, so the shipped fonts should work even
if they are not installed system-wide. Installing them on Windows is still fine
and may improve compatibility with some ImageMagick builds.

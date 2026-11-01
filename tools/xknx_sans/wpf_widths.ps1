param([string]$family, [double]$size, [string]$in, [string]$out)
# The width WPF gives each character of the input (one per line, as a hexadecimal code point) in
# the font family $family at $size device-independent pixels, with its own font fallback.
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase
$typeface = New-Object System.Windows.Media.Typeface($family)
$ideal = [System.Windows.Media.TextFormattingMode]::Ideal
$culture = [Globalization.CultureInfo]::InvariantCulture
$builder = New-Object System.Text.StringBuilder
foreach ($line in [IO.File]::ReadAllLines($in)) {
  $text = [char]::ConvertFromUtf32([Convert]::ToInt32($line, 16))
  $formatted = New-Object System.Windows.Media.FormattedText($text, $culture, [System.Windows.FlowDirection]::LeftToRight, $typeface, $size, [System.Windows.Media.Brushes]::Black, $null, $ideal, 1.0)
  [void]$builder.AppendLine($formatted.WidthIncludingTrailingWhitespace.ToString("R", $culture))
}
[IO.File]::WriteAllText($out, $builder.ToString())

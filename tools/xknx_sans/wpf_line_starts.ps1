param([string]$family, [double]$size, [string]$in, [string]$out)
# Each input line: <prefix>TAB<text>, with \uXXXX escapes. A TextBlock in $family at $size wraps
# <text> at the width of <prefix> (without trailing spaces) plus 0.01; the output line is the
# offset in <text> where its second line starts, or -1 for a single line.
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase
$source = @"
using System;
using System.Globalization;
using System.IO;
using System.Text;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
public static class LineStarts {
  static string Unescape(string s) {
    var builder = new StringBuilder();
    for (int i = 0; i < s.Length; i++) {
      if (s[i] == '\\' && i + 7 < s.Length && s[i + 1] == 'u') {
        builder.Append(char.ConvertFromUtf32(Convert.ToInt32(s.Substring(i + 2, 6), 16)));
        i += 7;
      } else builder.Append(s[i]);
    }
    return builder.ToString();
  }
  public static void Run(string family, double size, string inPath, string outPath) {
    var fontFamily = new FontFamily(family);
    var typeface = new Typeface(family);
    var result = new StringBuilder();
    foreach (var line in File.ReadAllLines(inPath, Encoding.UTF8)) {
      int tab = line.IndexOf('\t');
      string prefix = Unescape(line.Substring(0, tab));
      string text = Unescape(line.Substring(tab + 1));
      var formatted = new FormattedText(prefix, CultureInfo.InvariantCulture, FlowDirection.LeftToRight, typeface, size, Brushes.Black, null, TextFormattingMode.Ideal, 1.0);
      double width = formatted.Width + 0.01;
      var block = new TextBlock();
      block.FontFamily = fontFamily;
      block.FontSize = size;
      block.TextWrapping = TextWrapping.Wrap;
      block.Text = text;
      block.Measure(new Size(width, double.PositiveInfinity));
      block.Arrange(new Rect(0, 0, width, block.DesiredSize.Height));
      var start = block.ContentStart.GetLineStartPosition(1);
      // The offset counts the start of the text's run as well.
      result.AppendLine(start == null ? "-1" : (block.ContentStart.GetOffsetToPosition(start) - 1).ToString());
    }
    File.WriteAllText(outPath, result.ToString());
  }
}
"@
Add-Type -TypeDefinition $source -ReferencedAssemblies PresentationFramework, PresentationCore, WindowsBase, System.Xaml
[LineStarts]::Run($family, $size, $in, $out)

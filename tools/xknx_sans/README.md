# XKNX Sans

Builds the editor's font, XKNX Sans, and records where WPF breaks lines, so that text in the editor
is as wide and wraps at the same places as the parameter dialogs written for Windows (Segoe UI 12
for labels and values, Segoe UI Semilight 12 for the texts between them, Segoe UI Semibold for
headlines, laid out by WPF).

XKNX Sans draws Latin letters as Selawik does (Microsoft's open counterpart of Segoe UI) and the
others as Noto Sans does (both under the SIL Open Font License), in three faces with the weights,
advance widths and kerning of Segoe UI, Segoe UI Semilight and Semibold; Devanagari is left out.
Only metrics are taken from Segoe UI, no outlines. The fonts and the line-break table are
committed; these scripts are run only when they are to change.

Windows only (they read Segoe UI from the system and measure text with WPF), from the repository
root, with `NotoSans[wdth,wght].ttf` and its `OFL.txt` from
<https://github.com/google/fonts/tree/main/ofl/notosans> in one folder, and `selawk.ttf`,
`selawksl.ttf`, `selawksb.ttf` (from `Selawik_Release.zip` of release 1.01 of
<https://github.com/microsoft/Selawik>) with its `LICENSE.txt` in another:

```
uv run --with fonttools --with uharfbuzz --with skia-pathops python tools/xknx_sans/build.py --noto <folder>/NotoSans[wdth,wght].ttf --selawik <selawik folder>
uv run python tools/xknx_sans/record_line_breaks.py
```

- `build.py` writes `assets/fonts/XKNXSans-Regular.ttf`, `XKNXSans-Semilight.ttf`,
  `XKNXSans-Bold.ttf` and `XKNXSans-OFL.txt` of the editor. Characters Segoe UI lacks get the width WPF's fallback fonts
  give them (`wpf_widths.ps1`).
- `record_line_breaks.py` writes `widgets/line_break_table.py`: for each class of characters, the
  classes a line may start with after it, directly or after spaces, probed in WPF TextBlocks
  (`wpf_line_starts.ps1`). It reads the built font, so it runs after `build.py`.

How the editor applies them (kerning, rounding to 1/300 px, the rules at line ends) is described
in `apps/editor-gui/src/editor_gui/widgets/text_layout.py`.

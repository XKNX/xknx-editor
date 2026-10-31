# XKNX Sans

Builds the editor's font, XKNX Sans, and records where WPF breaks lines, so that text in the editor
is as wide and wraps at the same places as the parameter dialogs written for Windows (Segoe UI
Semilight 12 for texts, Segoe UI Semibold for headlines, laid out by WPF).

XKNX Sans is Noto Sans (SIL Open Font License, the successor of Droid Sans) instanced at the
weights of Segoe UI Semilight and Semibold, with their advance widths and kerning; Devanagari is
left out. Only metrics are taken from Segoe UI, no outlines. The fonts and the line-break table are
committed; these scripts are run only when they are to change.

Windows only (they read Segoe UI from the system and measure text with WPF), from the repository
root, with `NotoSans[wdth,wght].ttf` and its `OFL.txt` from
<https://github.com/google/fonts/tree/main/ofl/notosans> in one folder:

```
uv run --with fonttools --with uharfbuzz python tools/xknx_sans/build.py --noto <folder>/NotoSans[wdth,wght].ttf
uv run python tools/xknx_sans/record_line_breaks.py
```

- `build.py` writes `assets/fonts/XKNXSans-Regular.ttf`, `XKNXSans-Bold.ttf` and
  `XKNXSans-OFL.txt` of the editor. Characters Segoe UI lacks get the width WPF's fallback fonts
  give them (`wpf_widths.ps1`).
- `record_line_breaks.py` writes `widgets/line_break_table.py`: for each class of characters, the
  classes a line may start with after it, directly or after spaces, probed in WPF TextBlocks
  (`wpf_line_starts.ps1`). It reads the built font, so it runs after `build.py`.

How the editor applies them (kerning, rounding to 1/300 px, the rules at line ends) is described
in `apps/editor-gui/src/editor_gui/widgets/text_layout.py`.

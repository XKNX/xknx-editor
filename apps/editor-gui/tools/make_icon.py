"""Regenerate the committed app icons — run MANUALLY after changing the design, never during the
build (needs Pillow):

    uv run --with pillow python apps/editor-gui/tools/make_icon.py

Writes, all from the same drawing:
  - src/editor_gui/assets/app_settings/icon.png  (512 px window icon, browser favicon)
  - src/editor_gui/web/tray/icon.png             (64 px tray icon of the browser UI)
  - icon.ico                                     (Windows .exe icon, multi-size)
  - icon.icns                                    (macOS .app icon, multi-size)
  - homeassistant/xknx-editor/icon.png, logo.png (the Home Assistant app)

The icon is "XKNX" over "editor" in white on a near-black rounded square, in the editor's own bold
font (assets/fonts/DroidSans-Bold.ttf), so every OS draws the same pixels. Smaller sizes drop
words that would be an unreadable smear: below 64 px only "XKNX", at 24 px and less only "X".
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BG = (17, 17, 17, 255)  # near-black rounded square
FG = (255, 255, 255, 255)  # white wordmark

APP = Path(__file__).resolve().parent.parent
FONT = APP / "src" / "editor_gui" / "assets" / "fonts" / "DroidSans-Bold.ttf"

# The layout, in pixels of the 512 px icon: transparent margin, corner radius (share of the
# square), ink widths of the lines, letter spacing of "editor" (share of its font size), the gap
# between the lines and where the middle of the text sits.
_MARGIN = 6
_RADIUS = 0.22
_WIDTHS = {"XKNX": 452, "editor": 292, "X": 300}
_TRACKING = {"editor": 0.07}
_GAP = 49
_MIDDLE = 261.5


def _words(size: int) -> tuple[str, ...]:
    if size >= 64:
        return ("XKNX", "editor")
    return ("XKNX",) if size > 24 else ("X",)


def _ink(text: str, font_size: int) -> Image.Image:
    """``text`` as an 8-bit mask, cropped to its ink."""
    font = ImageFont.truetype(str(FONT), font_size)
    tracking = _TRACKING.get(text, 0.0) * font_size
    draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    width = sum(draw.textlength(ch, font=font) for ch in text) + tracking * len(text)
    mask = Image.new("L", (int(width) + 2 * font_size, 3 * font_size))
    draw = ImageDraw.Draw(mask)
    x = float(font_size)
    for ch in text:
        draw.text((x, font_size), ch, font=font, fill=255)
        x += draw.textlength(ch, font=font) + tracking
    return mask.crop(mask.getbbox())


def _fitted(text: str, width: float) -> Image.Image:
    """``text`` at the font size whose ink comes closest to ``width``."""
    low, high = 1, 4 * max(1, int(width))
    while low < high:
        mid = (low + high + 1) // 2
        if _ink(text, mid).width <= width:
            low = mid
        else:
            high = mid - 1
    return min(
        (_ink(text, low), _ink(text, low + 1)), key=lambda m: abs(m.width - width)
    )


def build(size: int, words: tuple[str, ...] | None = None) -> Image.Image:
    """The icon at ``size`` px as an RGBA image."""
    scale = size / 512
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    margin = round(_MARGIN * scale)
    side = size - 2 * margin
    ImageDraw.Draw(im).rounded_rectangle(
        [margin, margin, size - margin - 1, size - margin - 1],
        radius=round(_RADIUS * side),
        fill=BG,
    )
    lines = [_fitted(w, _WIDTHS[w] * scale) for w in (words or _words(size))]
    gap = round(_GAP * scale)
    middle = _MIDDLE * scale if len(lines) > 1 else size / 2
    y = round(middle - (sum(m.height for m in lines) + gap * (len(lines) - 1)) / 2)
    for mask in lines:
        im.paste(FG, (round((size - mask.width) / 2), y), mask)
        y += mask.height + gap
    return im


def main() -> None:
    icon = APP / "src" / "editor_gui" / "assets" / "app_settings" / "icon.png"
    tray = APP / "src" / "editor_gui" / "web" / "tray" / "icon.png"
    home_assistant = APP.parent.parent / "homeassistant" / "xknx-editor"
    for path in (icon, home_assistant / "icon.png", home_assistant / "logo.png"):
        build(512).save(path, optimize=True)
        print(f"wrote {path}")
    # Trays show it at 16-24 px: the one word only.
    build(64, ("XKNX",)).save(tray, optimize=True)
    print(f"wrote {tray}")

    # Windows .ico with the sizes Explorer and the taskbar use.
    ico_sizes = [16, 24, 32, 48, 64, 128, 256]
    build(256).save(
        APP / "icon.ico",
        format="ICO",
        sizes=[(s, s) for s in ico_sizes],
        append_images=[build(s) for s in ico_sizes],
    )
    print(f"wrote {APP / 'icon.ico'}")

    # macOS .icns with every size of an iconset (16 pt to 512 pt @2x).
    build(1024).save(
        APP / "icon.icns",
        format="ICNS",
        append_images=[build(s) for s in (16, 32, 64, 128, 256, 512)],
    )
    print(f"wrote {APP / 'icon.icns'}")


if __name__ == "__main__":
    main()

"""Build XKNX Sans: Selawik and Noto Sans at the weights of Segoe UI Semilight and Semibold, with
their widths.

Run on Windows (it reads Segoe UI from the system's font folder):

    uv run --with fonttools --with uharfbuzz --with skia-pathops python tools/xknx_sans/build.py         --noto <NotoSans[wdth,wght].ttf> --selawik <folder>

It writes, to ``apps/editor-gui/src/editor_gui/assets/fonts``:

- ``XKNXSans-Regular.ttf``, ``XKNXSans-Semilight.ttf`` and ``XKNXSans-Bold.ttf``: the Latin
  letters of Selawik Regular, Semilight and Semibold (Microsoft's open counterpart of Segoe UI; its
  Semibold thinned to the stems of Segoe UI Semibold), and the others of Noto Sans instanced at the
  weight whose stems are as thick as those of Segoe UI Regular, Semilight and Semibold. Each letter
  Segoe UI also has gets Segoe UI's advance width; its outline is stretched by half the difference
  (at most 8 %) and placed in the new width with the old ratio of its side bearings (half the
  change on each side for a glyph reaching over the edges of its advance). The kerning
  of Segoe UI goes into the font twice: as the "kern" feature (GPOS) for other programs, and as a
  "kern" table (format 0), which the editor reads to lay out text (``widgets/text_layout.py``).
- ``XKNXSans-OFL.txt``: the licences of Selawik and Noto Sans, which XKNX Sans keeps (both the
  SIL Open Font License; "Selawik" is a reserved font name, so the font has a name of its own).
"""

from __future__ import annotations

import argparse
import subprocess
import tempfile
import unicodedata
from collections.abc import Iterable
from itertools import pairwise
from pathlib import Path
from typing import Any

import pathops
import uharfbuzz as hb
from fontTools import subset
from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.recordingPen import DecomposingRecordingPen, RecordingPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.scaleUpem import scale_upem
from fontTools.ttLib.tables._k_e_r_n import KernTable_format_0
from fontTools.varLib.instancer import instantiateVariableFont

REPO = Path(__file__).resolve().parents[2]
SELAWIK_COPYRIGHT = (
    "Copyright 2015, Microsoft Corporation (www.microsoft.com), with Reserved Font Name "
    "Selawik. Selawik is a trademark of Microsoft Corporation in the United States and/or "
    "other countries."
)
OUT = REPO / "apps" / "editor-gui" / "src" / "editor_gui" / "assets" / "fonts"

# 3 x the 2048 units of Segoe UI, so its widths stay whole numbers, and a multiple of 6, so the
# 14 px ImGui font size (ascent plus descent) gives letters exactly 12 px high (the em).
UNITS_PER_EM = 6144
LINE_UNITS = UNITS_PER_EM * 14 // 12
# The ascent's share of the line, as in Droid Sans (1901 of 2384), the font before.
ASCENT = round(LINE_UNITS * 1901 / 2384)
# The share of a width difference taken by stretching the outline, and the most it stretches.
STRETCH_SHARE = 0.5
STRETCH_LIMIT = 0.08

FACES = {
    "regular": {
        "segoe": "segoeui.ttf",
        "selawik": "selawk.ttf",
        "family": "Segoe UI",
        "style": "Regular",
        "bold": False,
    },
    "semilight": {
        "segoe": "segoeuisl.ttf",
        "selawik": "selawksl.ttf",
        "family": "Segoe UI Semilight",
        "style": "Semilight",
        "bold": False,
    },
    "bold": {
        "segoe": "seguisb.ttf",
        "selawik": "selawksb.ttf",
        "family": "Segoe UI Semibold",
        "style": "Bold",
        "bold": True,
    },
}

# Devanagari, which Noto Sans has as well: ImGui cannot join its letters, so it is left out.
LEFT_OUT = ((0x0900, 0x097F), (0x1CD0, 0x1CFF), (0xA8E0, 0xA8FF))

# The characters whose pairs are kerned: Latin, Greek, Cyrillic and general punctuation.
KERNED_RANGES = (
    (0x20, 0x24F),
    (0x370, 0x3FF),
    (0x400, 0x52F),
    (0x2000, 0x206F),
    (0x20A0, 0x20CF),
)


def _stem(font: TTFont) -> float:
    glyph = font["glyf"][font.getBestCmap()[ord("l")]]
    return (glyph.xMax - glyph.xMin) / font["head"].unitsPerEm


def _weight_for(noto: Path, stem: float) -> float:
    """The weight of Noto Sans whose "l" stem is ``stem`` (in ems) thick."""
    low, high = 100.0, 900.0
    for _ in range(14):
        middle = (low + high) / 2
        font = instantiateVariableFont(TTFont(noto), {"wght": middle, "wdth": 100})
        if _stem(font) < stem:
            low = middle
        else:
            high = middle
    return round((low + high) / 2, 1)


def _instance(noto: Path, weight: float) -> TTFont:
    font = instantiateVariableFont(TTFont(noto), {"wght": weight, "wdth": 100})
    options = subset.Options()
    options.layout_features = []
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.notdef_outline = True
    options.hinting = False
    options.drop_tables += ["GSUB", "GPOS", "GDEF", "STAT"]
    subsetter = subset.Subsetter(options)
    subsetter.populate(
        unicodes=[
            code
            for code in font.getBestCmap()
            if not any(low <= code <= high for low, high in LEFT_OUT)
        ]
    )
    subsetter.subset(font)
    scale_upem(font, UNITS_PER_EM)
    _decompose(font)
    return font


def _decompose(font: TTFont) -> None:
    """Draw composite glyphs as plain outlines, so each glyph can be moved on its own."""
    glyf = font["glyf"]
    glyphs = font.getGlyphSet()
    for name in font.getGlyphOrder():
        if glyf[name].isComposite():
            recording = DecomposingRecordingPen(glyphs)
            glyphs[name].draw(recording)
            pen = TTGlyphPen(None)
            recording.replay(pen)
            glyf[name] = pen.glyph()
    font["maxp"].maxComponentElements = 0
    font["maxp"].maxComponentDepth = 0


def _selawik(path: Path, stem: float) -> TTFont:
    """Selawik ``path`` in XKNX Sans units, its stems thinned to ``stem`` (in ems) where they
    are thicker: each outline shrinks by half the difference on every side, and is then stretched
    back to its x-height."""
    font = TTFont(path)
    scale_upem(font, UNITS_PER_EM)
    _decompose(font)
    inset = (_stem(font) - stem) * UNITS_PER_EM / 2
    if inset <= 0:
        return font
    x_height = font["OS/2"].sxHeight
    grow = x_height / (x_height - 2 * inset)
    glyf = font["glyf"]
    glyphs = font.getGlyphSet()
    for name in font.getGlyphOrder():
        if not glyf[name].numberOfContours:
            continue
        outline = pathops.Path()
        glyphs[name].draw(pathops.PathPen(outline, glyphs))
        edge = pathops.Path()
        glyphs[name].draw(pathops.PathPen(edge, glyphs))
        edge.stroke(2 * inset, pathops.LineCap.BUTT_CAP, pathops.LineJoin.MITER_JOIN, 4)
        thinned = pathops.op(outline, edge, pathops.PathOp.DIFFERENCE)
        pen = TTGlyphPen(None)
        thinned.draw(
            Cu2QuPen(TransformPen(pen, (1, 0, 0, grow, 0, -inset * grow)), max_err=1)
        )
        glyf[name] = pen.glyph()
    return font


def _take_latin(font: TTFont, selawik: TTFont) -> None:
    """The letters Selawik has, drawn as Selawik draws them, in ``font``."""
    cmap = font.getBestCmap()
    glyphs = selawik.getGlyphSet()
    for code, name in selawik.getBestCmap().items():
        if code not in cmap:
            continue
        pen = TTGlyphPen(None)
        glyphs[name].draw(pen)
        font["glyf"][cmap[code]] = pen.glyph()
        font["hmtx"][cmap[code]] = selawik["hmtx"][name]
        font["glyf"][cmap[code]].recalcBounds(font["glyf"])


def _segoe_heights(font: TTFont, segoe: TTFont, codes: Iterable[int]) -> None:
    """The glyphs of ``codes`` as high and as deep as Segoe UI draws them: each outline stretched
    vertically, piecewise, so that its x-height (where it reaches above it), its top and its
    bottom are those of Segoe UI's glyph, the baseline staying. Thinning a face's outlines and
    stretching them back to its x-height (see _selawik) lifts its capitals and ascenders, which
    would then reach a pixel higher than Segoe UI's."""
    scale = font["head"].unitsPerEm / segoe["head"].unitsPerEm
    ours, theirs = font.getGlyphSet(), segoe.getGlyphSet()
    cmap, segoe_cmap = font.getBestCmap(), segoe.getBestCmap()

    def bounds(
        glyphs: Any, name: str, factor: float = 1.0
    ) -> tuple[float, float] | None:
        pen = BoundsPen(glyphs)
        glyphs[name].draw(pen)
        if pen.bounds is None:
            return None
        return pen.bounds[1] * factor, pen.bounds[3] * factor

    x_ours = bounds(ours, cmap[ord("x")])
    x_theirs = bounds(theirs, segoe_cmap[ord("x")], scale)
    assert x_ours is not None and x_theirs is not None
    x_height, segoe_x_height = x_ours[1], x_theirs[1]
    glyf = font["glyf"]
    for code in sorted(set(codes) & set(cmap) & set(segoe_cmap)):
        name = cmap[code]
        own = bounds(ours, name)
        target = bounds(theirs, segoe_cmap[code], scale)
        if own is None or target is None:
            continue
        (bottom, top), (segoe_bottom, segoe_top) = own, target
        knots = [(0.0, 0.0)]
        if top > x_height and segoe_top > segoe_x_height:
            knots.append((x_height, segoe_x_height))
        if top > 0 and segoe_top > 0:
            knots.append((top, segoe_top))
        if bottom < 0 and segoe_bottom < 0:
            knots.insert(0, (bottom, segoe_bottom))

        if len(knots) < 2:
            continue

        def lift(y: float, knots: list[tuple[float, float]] = knots) -> float:
            # Linear between the knots (and beyond the first and the last one).
            segment = knots[0], knots[1]
            for segment in pairwise(knots):
                if y <= segment[1][0]:
                    break
            (y0, t0), (y1, t1) = segment
            return t0 + (y - y0) * (t1 - t0) / (y1 - y0)

        recording = RecordingPen()
        ours[name].draw(recording)
        pen = TTGlyphPen(None)
        for operator, points in recording.value:
            getattr(pen, operator)(*((x, lift(y)) for x, y in points))
        glyf[name] = pen.glyph()
        glyf[name].recalcBounds(glyf)


def _clone(font: TTFont, name: str) -> str:
    """A copy of glyph ``name`` under a new name, for a character Segoe UI gives another width."""
    glyf, hmtx = font["glyf"], font["hmtx"]
    order = font.getGlyphOrder()
    index = 1
    while f"{name}.w{index}" in glyf:
        index += 1
    clone = f"{name}.w{index}"
    pen = TTGlyphPen(None)
    font.getGlyphSet()[name].draw(pen)
    glyf[clone] = pen.glyph()
    hmtx[clone] = hmtx[name]
    font.setGlyphOrder([*order, clone])
    return clone


def _fit(font: TTFont, name: str, advance: int) -> None:
    """Give glyph ``name`` the advance width ``advance``."""
    glyf, hmtx = font["glyf"], font["hmtx"]
    glyph = glyf[name]
    old_advance, _ = hmtx[name]
    if not glyph.numberOfContours or old_advance == 0:
        hmtx[name] = (advance, 0 if not glyph.numberOfContours else hmtx[name][1])
        return
    glyph.recalcBounds(glyf)
    left, right = glyph.xMin, old_advance - glyph.xMax
    ink = glyph.xMax - glyph.xMin
    ratio = advance / old_advance
    stretch = 1 + max(-STRETCH_LIMIT, min(STRETCH_LIMIT, (ratio - 1) * STRETCH_SHARE))
    new_ink = ink * stretch
    room = advance - new_ink
    bearings = left + right
    if left >= 0 and right >= 0 and bearings > 0:
        new_left = room * left / bearings
    else:
        # A glyph reaching over its edges (as a "J" or "f" may): half the change on each side.
        new_left = left + (room - bearings) / 2
    coordinates = glyph.coordinates
    for i, (x, y) in enumerate(coordinates):
        coordinates[i] = (round((x - glyph.xMin) * stretch + new_left), y)
    glyph.recalcBounds(glyf)
    hmtx[name] = (advance, glyph.xMin)


def _fallback_widths(family: str, codes: list[int]) -> dict[int, int]:
    """The widths WPF gives ``codes`` in ``family``, which lacks them, from its fallback fonts.

    At a size of one font unit per device-independent pixel, the width is the advance in units.
    """
    with tempfile.TemporaryDirectory() as folder:
        source, result = Path(folder) / "in.txt", Path(folder) / "out.txt"
        source.write_text("\n".join(f"{code:X}" for code in codes), encoding="ascii")
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(Path(__file__).with_name("wpf_widths.ps1")),
                "-family",
                family,
                "-size",
                str(UNITS_PER_EM),
                "-in",
                str(source),
                "-out",
                str(result),
            ],
            check=True,
        )
        widths = result.read_text(encoding="utf-8-sig").split()
    return {
        code: round(float(width)) for code, width in zip(codes, widths, strict=True)
    }


def _apply_widths(font: TTFont, segoe: TTFont, family: str) -> dict[int, int]:
    """Segoe UI's advance width for every character both fonts have, and WPF's width (from its
    fallback fonts) for the others; the advances of all characters of the font."""
    cmap = font.getBestCmap()
    segoe_cmap = segoe.getBestCmap()
    scale = UNITS_PER_EM // segoe["head"].unitsPerEm
    fallback = _fallback_widths(family, sorted(c for c in cmap if c not in segoe_cmap))
    wanted: dict[str, dict[int, list[int]]] = {}
    for code, name in cmap.items():
        if code in segoe_cmap:
            advance = segoe["hmtx"][segoe_cmap[code]][0] * scale
        else:
            advance = fallback[code]
        wanted.setdefault(name, {}).setdefault(advance, []).append(code)
    for name, by_advance in wanted.items():
        first, *others = sorted(by_advance.items(), key=lambda item: -len(item[1]))
        for advance, codes in others:
            clone = _clone(font, name)
            _fit(font, clone, advance)
            for table in font["cmap"].tables:
                for code in codes:
                    if code in table.cmap:
                        table.cmap[code] = clone
        _fit(font, name, first[0])
    cmap = font.getBestCmap()
    return {code: font["hmtx"][name][0] for code, name in cmap.items()}


def _script(code: int) -> str:
    """ "Latn", "Grek", "Cyrl", or "" for a character of no script (digits, punctuation)."""
    name = unicodedata.name(chr(code), "")
    for script, word in (("Latn", "LATIN"), ("Grek", "GREEK"), ("Cyrl", "CYRILLIC")):
        if name.startswith(word):
            return script
    return ""


def _kerning(segoe_path: Path, codes: list[int]) -> dict[int, dict[int, int]]:
    """The kerning of each pair of ``codes`` in Segoe UI, in XKNX Sans units.

    WPF kerns a pair only within a run of one script, so letters of two scripts are not kerned.
    WPF does not kern a hyphen-minus against the character before it either.
    """
    font = hb.Font(hb.Face(hb.Blob.from_file_path(str(segoe_path))))
    segoe = TTFont(segoe_path)
    scale = UNITS_PER_EM // segoe["head"].unitsPerEm
    single: dict[int, int] = {}

    def shape(text: str) -> list[int]:
        buffer = hb.Buffer()
        buffer.add_str(text)
        buffer.guess_segment_properties()
        hb.shape(font, buffer, {"kern": True})
        return [p.x_advance for p in buffer.glyph_positions]

    lefts, rights = _kerned_glyphs(segoe)
    cmap = segoe.getBestCmap()
    usable = [c for c in codes if unicodedata.category(chr(c))[0] not in "MC"]
    for code in usable:
        single[code] = shape(chr(code))[0]
    kerning: dict[int, dict[int, int]] = {}
    for left in usable:
        if cmap[left] not in lefts:
            continue
        for right in usable:
            if right == 0x2D or cmap[right] not in rights:
                continue
            scripts = {_script(left), _script(right)} - {""}
            if len(scripts) > 1:
                continue
            advances = shape(chr(left) + chr(right))
            if len(advances) != 2:
                continue
            value = advances[0] - single[left]
            if value:
                kerning.setdefault(left, {})[right] = value * scale
    return kerning


def _kerned_glyphs(segoe: TTFont) -> tuple[set[str], set[str]]:
    """The glyphs that can be the first and the second of a kerned pair (the pair positioning
    lookups of the "kern" feature)."""
    gpos = segoe["GPOS"].table
    lookups: set[int] = set()
    for record in gpos.FeatureList.FeatureRecord:
        if record.FeatureTag == "kern":
            lookups.update(record.Feature.LookupListIndex)
    lefts: set[str] = set()
    rights: set[str] = set()
    for index in lookups:
        lookup = gpos.LookupList.Lookup[index]
        for table in lookup.SubTable:
            kind = lookup.LookupType
            if kind == 9:
                kind, table = table.ExtensionLookupType, table.ExtSubTable
            if kind != 2:
                continue
            lefts.update(table.Coverage.glyphs)
            if table.Format == 1:
                for pair_set in table.PairSet:
                    rights.update(
                        record.SecondGlyph for record in pair_set.PairValueRecord
                    )
            else:
                rights.update(table.ClassDef2.classDefs)
                # Class 0 holds every glyph not listed in the class definition.
                rights.update(segoe.getGlyphOrder())
    return lefts, rights


def _add_kerning(font: TTFont, kerning: dict[int, dict[int, int]]) -> None:
    """``kerning`` (by character) as the font's "kern" table and "kern" feature."""
    cmap = font.getBestCmap()
    by_glyph: dict[tuple[str, str], int] = {}
    for left, pairs in kerning.items():
        for right, value in pairs.items():
            by_glyph.setdefault((cmap[left], cmap[right]), value)
    table = newTable("kern")
    table.version = 0
    subtable = KernTable_format_0()
    subtable.version, subtable.coverage, subtable.format = 0, 1, 0
    subtable.kernTable = dict(by_glyph)
    table.kernTables = [subtable]
    font["kern"] = table
    rules = "\n".join(f"  pos {a} {b} {v};" for (a, b), v in sorted(by_glyph.items()))
    languages = "\n".join(
        f"languagesystem {script} dflt;" for script in ("DFLT", "latn", "grek", "cyrl")
    )
    addOpenTypeFeaturesFromString(
        font, f"{languages}\nfeature kern {{\n{rules}\n}} kern;\n"
    )


def _name(font: TTFont, style: str, bold: bool, weight: int) -> None:
    family = "XKNX Sans"
    full = family if style == "Regular" else f"{family} {style}"
    postscript = f"XKNXSans-{style}"
    names = font["name"]
    copyright_text = names.getDebugName(0) or ""
    keep = {0, 13, 14}
    names.names = [n for n in names.names if n.nameID in keep]
    names.setName(
        f"{SELAWIK_COPYRIGHT} {copyright_text} Copyright 2026 xknx.", 0, 3, 1, 0x409
    )
    for name_id, text in (
        (1, family),
        (2, style),
        (3, f"{postscript};1.000"),
        (4, full),
        (5, "Version 1.000"),
        (6, postscript),
        (
            10,
            "Selawik and Noto Sans with the widths and stem weight of Segoe UI "
            + ("Semibold" if bold else "Semilight")
            + ".",
        ),
    ):
        names.setName(text, name_id, 3, 1, 0x409)
        names.setName(text, name_id, 1, 0, 0)
    os2 = font["OS/2"]
    os2.usWeightClass = weight
    # Bold, regular, or neither (Semilight).
    selection = 0b100000 if bold else 0b1000000 if style == "Regular" else 0
    os2.fsSelection = (os2.fsSelection & ~0b1100001) | selection
    font["head"].macStyle = 1 if bold else 0
    font["post"].italicAngle = 0


def _vertical(font: TTFont) -> None:
    descent = LINE_UNITS - ASCENT
    hhea, os2 = font["hhea"], font["OS/2"]
    hhea.ascent, hhea.descent, hhea.lineGap = ASCENT, -descent, 0
    os2.sTypoAscender, os2.sTypoDescender, os2.sTypoLineGap = ASCENT, -descent, 0
    os2.usWinAscent, os2.usWinDescent = ASCENT, descent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--noto", type=Path, required=True, help="NotoSans[wdth,wght].ttf"
    )
    parser.add_argument(
        "--selawik",
        type=Path,
        required=True,
        help="folder with selawk.ttf, selawksl.ttf, selawksb.ttf and LICENSE.txt (Selawik 1.01)",
    )
    parser.add_argument("--segoe-dir", type=Path, default=Path("C:/Windows/Fonts"))
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    for face in FACES.values():
        segoe_path = args.segoe_dir / str(face["segoe"])
        segoe = TTFont(segoe_path)
        weight = _weight_for(args.noto, _stem(segoe))
        font = _instance(args.noto, weight)
        selawik = _selawik(args.selawik / str(face["selawik"]), _stem(segoe))
        _take_latin(font, selawik)
        if face["bold"]:
            # The face whose outlines are thinned.
            _segoe_heights(font, segoe, selawik.getBestCmap())
        advances = _apply_widths(font, segoe, str(face["family"]))
        segoe_codes = segoe.getBestCmap()
        codes = sorted(
            c
            for c in advances
            if c in segoe_codes and any(lo <= c <= hi for lo, hi in KERNED_RANGES)
        )
        kerning = _kerning(segoe_path, codes)
        _add_kerning(font, kerning)
        _vertical(font)
        _name(font, str(face["style"]), bool(face["bold"]), segoe["OS/2"].usWeightClass)
        # Noto Sans' own date, so a build of the same sources gives the same file.
        font["head"].modified = font["head"].created
        font.recalcTimestamp = False
        font.save(args.out / f"XKNXSans-{face['style']}.ttf")
        pairs = sum(len(p) for p in kerning.values())
        print(
            f"{face['style']}: weight {weight}, {len(advances)} characters, {pairs} kerned pairs"
        )
    licences = "\n\n".join(
        (
            "XKNX Sans contains Selawik and Noto Sans, both under the SIL Open Font License.",
            "Selawik:",
            (args.selawik / "LICENSE.txt").read_text(encoding="utf-8").strip(),
            "Noto Sans:",
            args.noto.with_name("OFL.txt").read_text(encoding="utf-8").strip(),
        )
    )
    (args.out / "XKNXSans-OFL.txt").write_text(
        licences + "\n", encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    main()

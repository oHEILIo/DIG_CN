#!/usr/bin/env python3
"""Generate BMFont v3 binary (.fnt + .tga) from TTF fonts with ordered multi-font fallback."""

import struct, os, glob
from PIL import Image, ImageFont, ImageDraw

try:
    from fontTools.ttLib import TTFont
except ImportError:
    TTFont = None
    print("⚠ fontTools not installed; glyph coverage detection disabled.")
    print("  Install with: pip install fonttools")

# Directory containing UTF-16 LE .tsv source files
TSV_DIR = r""

# ── Configuration ─────────────────────────────────────────────────
FONT_SIZE   = 88
LINE_HEIGHT = 139
BASE        = 110
TEX_W       = 1024
TEX_H       = 1024
PADDING     = 4
OUTPUT_DIR  = "font"
PAGE_PREFIX = "font_"
FONT_NAME   = "NotoSans"

# Ordered font dict: for each glyph, fonts are tried top-to-bottom
# until one provides coverage. (Python 3.7+ preserves insertion order.)
FONT_FILES = {
    "NotoSansSC": "NotoSansSC-Regular.ttf",
    # "NotoSansJP": "NotoSansJP-Regular.ttf",
}

# ── Font Loading ──────────────────────────────────────────────────
def load_cmap(ttf_path):
    """Return set of Unicode codepoints covered by a font, or None if unavailable."""
    if TTFont is None or not os.path.isfile(ttf_path):
        return None
    tt = TTFont(ttf_path, lazy=True)
    cmap = tt.getBestCmap()
    tt.close()
    return set(cmap.keys()) if cmap else set()


def load_fonts():
    """Load all fonts defined in FONT_FILES in order.
    Returns list of (name, ImageFont, cmap_or_None) tuples."""
    fonts = []
    for name, path in FONT_FILES.items():
        if not os.path.isfile(path):
            print(f"  ⚠ '{path}' ({name}) not found, skipping.")
            continue
        font = ImageFont.truetype(path, FONT_SIZE)
        cmap = load_cmap(path)
        info = f"  ({len(cmap)} glyphs)" if cmap else ""
        print(f"  Loaded: {path} [{name}]{info}")
        fonts.append((name, font, cmap))

    if not fonts:
        raise FileNotFoundError("No usable font files found. Check FONT_FILES configuration.")
    return fonts


# ── Charset Collection ────────────────────────────────────────────
def build_charset():
    """Read all .tsv files (UTF-16 LE) from TSV_DIR, collect unique characters,
    ensure ASCII printable range is included, return sorted list."""
    chars = set()
    tsv_files = glob.glob(os.path.join(TSV_DIR, "*.tsv"))
    if not tsv_files:
        raise FileNotFoundError(f"No .tsv files found in {TSV_DIR}")
    print(f"  Reading {len(tsv_files)} .tsv file(s) from {TSV_DIR} ...")

    for path in tsv_files:
        with open(path, "r", encoding="utf-16-le") as f:
            text = f.read()
        chars.update(text.lstrip("\ufeff"))

    # Remove control characters (keep space)
    for c in ('\r', '\n', '\t', '\x00'):
        chars.discard(c)

    # Always include basic ASCII printable range
    chars.update(chr(c) for c in range(0x20, 0x7F))
    return sorted(chars, key=ord)


# ── Glyph Rendering ──────────────────────────────────────────────
def select_font(cp, fonts):
    """Return (name, ImageFont) for the first font covering codepoint `cp`,
    or (None, None) if none does."""
    for name, font, cmap in fonts:
        if cmap is None or cp in cmap:   # cmap is None → fontTools unavailable, try blindly
            return name, font
    return None, None


def render_glyphs(fonts, charset):
    """Render each character with the first font that covers it.
    Returns list of glyph dicts ready for atlas packing."""
    glyphs = []
    missing = []
    fallback_usage = {}           # font_name → [chars] (non-primary only)
    primary_name = fonts[0][0]

    for ch in charset:
        cp = ord(ch)
        name, font = select_font(cp, fonts)

        if font is None:
            missing.append(ch)
            continue

        if name != primary_name:
            fallback_usage.setdefault(name, []).append(ch)

        # Measure glyph
        bbox = font.getbbox(ch)
        if not bbox:
            continue
        l, t, r, b = bbox
        w, h = r - l, b - t
        adv = int(font.getlength(ch))

        if w <= 0 or h <= 0:
            if ch == ' ':
                glyphs.append(dict(id=cp, w=0, h=0,
                                   xoff=0, yoff=0, xadv=adv, img=None))
            continue

        # Render glyph into a padded RGBA image
        pw, ph = w + 2 * PADDING, h + 2 * PADDING
        img = Image.new('RGBA', (pw, ph), (0, 0, 0, 0))
        ImageDraw.Draw(img).text((PADDING - l, PADDING - t), ch,
                                 font=font, fill=(255, 255, 255, 255))
        glyphs.append(dict(id=cp, w=pw, h=ph,
                           xoff=l - PADDING, yoff=t - PADDING,
                           xadv=adv, img=img))

    # ── Report ──
    for name, chars in fallback_usage.items():
        print(f"  ⚠ {len(chars)} char(s) rendered via fallback '{name}':")
        for ch in chars[:30]:
            print(f"      U+{ord(ch):04X}  '{ch}'")
        if len(chars) > 30:
            print(f"      ... and {len(chars) - 30} more")

    if missing:
        print(f"\n  ❌ {len(missing)} char(s) missing from ALL fonts (skipped):")
        for ch in missing[:30]:
            print(f"      U+{ord(ch):04X}  '{ch}'")
        if len(missing) > 30:
            print(f"      ... and {len(missing) - 30} more")
        print("  → Add a fallback font or remove these characters from source data.")

    return glyphs


# ── Atlas Packing ─────────────────────────────────────────────────
def pack_glyphs(glyphs):
    """Shelf-pack rendered glyphs into texture atlas pages.
    Returns (pages: list[Image], entries: list[dict])."""
    pages, entries = [], []
    page = Image.new('RGBA', (TEX_W, TEX_H), (0, 0, 0, 0))
    pi = x = y = row_h = 0

    for g in glyphs:
        if g['img'] is None:
            entries.append(dict(id=g['id'], x=0, y=0, w=0, h=0,
                                xoff=0, yoff=0, xadv=g['xadv'], page=0, chnl=15))
            continue

        gw, gh = g['w'], g['h']

        # Wrap to next row
        if x + gw > TEX_W:
            x, y, row_h = 0, y + row_h, 0

        # Overflow to next page
        if y + gh > TEX_H:
            pages.append(page)
            page = Image.new('RGBA', (TEX_W, TEX_H), (0, 0, 0, 0))
            pi += 1
            x = y = row_h = 0

        page.paste(g['img'], (x, y))
        entries.append(dict(id=g['id'], x=x, y=y, w=gw, h=gh,
                            xoff=g['xoff'], yoff=g['yoff'], xadv=g['xadv'],
                            page=pi, chnl=15))
        x += gw
        row_h = max(row_h, gh)

    pages.append(page)
    return pages, entries


# ── File Output ───────────────────────────────────────────────────
def save_pages(pages):
    """Save atlas pages as 32-bit RGBA uncompressed TGA files."""
    names = []
    for i, img in enumerate(pages):
        name = f"{PAGE_PREFIX}{i:02d}.tga"
        names.append(name)
        img.save(os.path.join(OUTPUT_DIR, name))
        print(f"  Saved {name} ({TEX_W}×{TEX_H})")
    return names


def build_fnt(entries, page_names):
    """Serialize glyph data into BMFont v3 binary (.fnt) format."""
    def block(btype, data):
        return struct.pack('<BI', btype, len(data)) + data

    fnt = bytearray(b'BMF\x03')

    # Block 1 – Info
    info = struct.pack('<hBBH8B', -FONT_SIZE, 0xC0, 0, 100,
                       1, 1, 1, 1, 1, 1, 1, 0)
    info += FONT_NAME.encode('utf-8') + b'\x00'
    fnt += block(1, info)

    # Block 2 – Common
    fnt += block(2, struct.pack('<5H5B',
        LINE_HEIGHT, BASE, TEX_W, TEX_H, len(page_names),
        0, 4, 0, 0, 0))

    # Block 3 – Page names (null-terminated)
    fnt += block(3, b''.join(n.encode('utf-8') + b'\x00' for n in page_names))

    # Block 4 – Character entries (20 bytes each)
    fnt += block(4, b''.join(
        struct.pack('<IHHHHhhhBB',
                    e['id'], e['x'], e['y'], e['w'], e['h'],
                    e['xoff'], e['yoff'], e['xadv'], e['page'], e['chnl'])
        for e in entries))

    return bytes(fnt)


# ── Main ──────────────────────────────────────────────────────────
def generate():
    """Entry point: load fonts → collect charset → render → pack → save."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("── Loading fonts ──")
    fonts = load_fonts()

    print("\n── Building charset ──")
    charset = build_charset()
    print(f"  {len(charset)} unique characters")

    print("\n── Rendering glyphs ──")
    glyphs = render_glyphs(fonts, charset)
    print(f"  {len(glyphs)} glyphs rendered")

    print("\n── Packing atlas ──")
    pages, entries = pack_glyphs(glyphs)
    print(f"  {len(pages)} page(s)")

    print("\n── Saving files ──")
    page_names = save_pages(pages)
    fnt_data = build_fnt(entries, page_names)
    fnt_path = os.path.join(OUTPUT_DIR, "font.fnt")
    with open(fnt_path, 'wb') as f:
        f.write(fnt_data)
    print(f"  Saved {fnt_path} ({len(fnt_data)} bytes, {len(entries)} char entries)")

    print("\n✅ Done.")


if __name__ == '__main__':
    generate()
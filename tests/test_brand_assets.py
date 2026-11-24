"""Locked Unplugged assets: size, transparency, and the A2 path shared by the mark files."""

import re
import struct
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
IMAGES = REPO / "docs" / "images"


def _png(path: Path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos = 8
    width = height = bit = color = None
    idat = []
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        kind = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, bit, color = struct.unpack(">IIBB", chunk[:10])
        elif kind == b"IDAT":
            idat.append(chunk)
        elif kind == b"IEND":
            break
    raw = zlib.decompress(b"".join(idat))
    assert (bit, color) == (8, 6)  # 8-bit RGBA
    stride = width * 4
    rows = []
    prev = bytearray(stride)
    i = 0
    for _ in range(height):
        filt = raw[i]
        i += 1
        row = bytearray(raw[i : i + stride])
        i += stride
        for x in range(stride):
            left = row[x - 4] if x >= 4 else 0
            up = prev[x]
            up_left = prev[x - 4] if x >= 4 else 0
            if filt == 0:
                pred = 0
            elif filt == 1:
                pred = left
            elif filt == 2:
                pred = up
            elif filt == 3:
                pred = (left + up) // 2
            elif filt == 4:
                pred = _paeth(left, up, up_left)
            else:
                raise AssertionError(f"unknown png filter {filt}")
            row[x] = (row[x] + pred) & 255
        prev = row
        rows.append(bytes(row))
    return width, height, rows


def _paeth(left, up, up_left):
    estimate = left + up - up_left
    pa, pb, pc = abs(estimate - left), abs(estimate - up), abs(estimate - up_left)
    if pa <= pb and pa <= pc:
        return left
    if pb <= pc:
        return up
    return up_left


def _px(rows, x, y):
    i = x * 4
    return rows[y][i : i + 4]


def _path(svg: Path) -> str:
    match = re.search(r'\sd="([^"]+)"', svg.read_text())
    assert match, svg
    return match.group(1)


def test_icon_png_is_400_rgba_with_transparent_corners():
    width, height, rows = _png(IMAGES / "icon.png")
    assert (width, height) == (400, 400)
    for corner in ((0, 0), (399, 0), (0, 399), (399, 399)):
        assert _px(rows, *corner)[3] == 0
    # The tile face is charcoal; the middle of the cloud is Warm Persimmon.
    assert _px(rows, 32, 32)[:3] == bytes((0x1B, 0x19, 0x18))
    assert _px(rows, 200, 200)[:3] == bytes((0xF0, 0x78, 0x4B))


def test_social_preview_png_is_1280x640_and_inset():
    width, height, rows = _png(IMAGES / "social-preview.png")
    assert (width, height) == (1280, 640)
    assert _px(rows, 0, 0) == bytes((0x1B, 0x19, 0x18, 255))
    # Cream wordmark ink stays off the edges. The #2A2725 frame is the outer accent.
    cream = []
    for y, row in enumerate(rows):
        for x in range(width):
            r, g, b, a = _px(rows, x, y)
            if a == 255 and r > 180 and g > 170 and b > 150:
                cream.append((x, y))
    xs = [p[0] for p in cream]
    ys = [p[1] for p in cream]
    assert cream, "wordmark did not render"
    assert min(xs) > 80 and max(xs) < width - 80
    assert min(ys) > 40 and max(ys) < height - 40


def test_mark_paths_match_and_colors_stay_split():
    mark = _path(IMAGES / "icon-mark.svg")
    mono = IMAGES / "icon-mono.svg"
    mask = REPO / "web" / "js" / "icon-mono.svg"
    assert _path(mono) == mark
    assert _path(mask) == mark
    assert _path(IMAGES / "icon.svg") != mark  # the tile uses the inset A2 path
    assert 'fill="#F0784B"' in (IMAGES / "icon-mark.svg").read_text()
    assert 'fill="currentColor"' in mono.read_text()
    assert "<rect" not in mono.read_text()
    tile = (IMAGES / "icon.svg").read_text()
    assert 'viewBox="0 0 64 64"' in tile
    assert 'fill="#1B1918"' in tile and 'stroke="#2A2725"' in tile and 'rx="14.08"' in tile


def test_registry_metadata_points_at_this_repo():
    text = (REPO / "pyproject.toml").read_text()
    assert 'Bug Tracker" = "https://github.com/byvfx/comfyless/issues"' in text
    assert "docs/images/icon.png" in text
    assert "comfy-serverless" not in text

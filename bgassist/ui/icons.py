"""The "Attend" mark (D18), drawn in code rather than shipped as a binary.

An open ring broken at the base with a solid centre. Two shapes only, which is
what lets it survive 16 px in the menu bar and the flattening to a black-and-
alpha template image. The four tray states come from the same two shapes:

    idle       small centre
    listening  the centre dilates
    thinking   the gap travels round the ring
    speaking   the gap opens wider, centre pulsing

They map onto the existing ``State`` enum, which already emits exactly these
transitions.

The halo (arcs stacked over a dot) was rejected deliberately: it is the
AirPort/wifi glyph and would have sat inches from the real one in the same
menu bar (D18a).

Everything here is pure Python — a tiny PNG encoder and a supersampled
rasteriser — so the icon ladder can be regenerated on any machine, with no
image library and no binary assets in the repository.
"""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

# Deep slate ground, soft aqua mark (D18).
GROUND = (24, 30, 38, 255)
MARK = (126, 214, 214, 255)

STATES = ("idle", "listening", "thinking", "speaking")

#: Squircle geometry for the app icon: half-width and the superellipse
#: exponent. 0.4 leaves the ~10% bezel padding macOS expects around the mark.
_SQUIRCLE_HALF = 0.4
_SQUIRCLE_EXPONENT = 5.0
#: How much of the frame the mark occupies inside the app icon's ground.
_MARK_SCALE = 0.66

RGBA = Tuple[int, int, int, int]


# -- a very small PNG writer ---------------------------------------------

def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def encode_png(width: int, height: int, pixels: Sequence[RGBA]) -> bytes:
    """RGBA pixels (row-major) -> PNG bytes."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type: none
        for x in range(width):
            raw.extend(pixels[y * width + x])
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + _chunk(b"IEND", b""))


# -- the mark -------------------------------------------------------------

def _coverage(size: int, state: str, samples: int = 4,
              scale: float = 1.0, bold: bool = False) -> List[float]:
    """Per-pixel coverage of the mark, supersampled for smooth edges.

    *scale* shrinks the mark inside the frame. The tray template fills its
    frame (1.0); the app icon leaves room inside the squircle ground. *bold*
    thickens the ring, because a 1.4 px stroke disappears in the menu bar.
    """
    ring_outer = 0.42 * scale
    stroke = {"idle": 0.09, "listening": 0.09, "thinking": 0.09,
              "speaking": 0.11}[state] * scale * (1.5 if bold else 1.0)
    ring_inner = ring_outer - stroke
    centre_radius = {"idle": 0.11, "listening": 0.17, "thinking": 0.13,
                     "speaking": 0.19}[state] * scale * (1.25 if bold else 1.0)
    gap_width = {"idle": 46.0, "listening": 46.0, "thinking": 52.0,
                 "speaking": 74.0}[state]
    gap_centre = {"idle": 90.0, "listening": 90.0, "thinking": 210.0,
                  "speaking": 90.0}[state]

    half = gap_width / 2.0
    coverage = [0.0] * (size * size)
    step = 1.0 / samples
    weight = 1.0 / (samples * samples)
    pixel = 1.0 / size
    # Only pixels near an edge need supersampling; the interior and the empty
    # ground are decided by one sample. At 1024 x 1024 that is the difference
    # between a second and half a minute.
    margin = 1.5 * pixel
    boundaries = (centre_radius, ring_inner, ring_outer)

    def solid(x: float, y: float) -> bool:
        distance = math.hypot(x, y)
        if distance <= centre_radius:
            return True
        if ring_inner <= distance <= ring_outer:
            angle = math.degrees(math.atan2(y, x)) % 360.0
            delta = abs((angle - gap_centre + 180.0) % 360.0 - 180.0)
            return delta > half
        return False

    for py in range(size):
        for px in range(size):
            cx = (px + 0.5) * pixel - 0.5
            cy = (py + 0.5) * pixel - 0.5
            distance = math.hypot(cx, cy)
            near_edge = any(abs(distance - b) < margin for b in boundaries)
            if not near_edge and distance > 0.0:
                angle = math.degrees(math.atan2(cy, cx)) % 360.0
                delta = abs((angle - gap_centre + 180.0) % 360.0 - 180.0)
                near_edge = (ring_inner - margin <= distance <= ring_outer + margin
                             and abs(delta - half) < 4.0)
            if not near_edge:
                coverage[py * size + px] = 1.0 if solid(cx, cy) else 0.0
                continue
            hits = 0.0
            for sy in range(samples):
                for sx in range(samples):
                    x = (px + (sx + 0.5) * step) * pixel - 0.5
                    y = (py + (sy + 0.5) * step) * pixel - 0.5
                    if solid(x, y):
                        hits += weight
            coverage[py * size + px] = hits
    return coverage


def _blend(background: RGBA, foreground: RGBA, alpha: float) -> RGBA:
    return tuple(  # type: ignore[return-value]
        int(round(background[i] * (1 - alpha) + foreground[i] * alpha))
        for i in range(4))


def render(size: int = 64, state: str = "idle", template: bool = False,
           ground: RGBA = GROUND, mark: RGBA = MARK) -> bytes:
    """PNG bytes for one icon.

    *template* produces the menu-bar form: pure black plus alpha, nothing else,
    so macOS tints it correctly in light and dark menu bars.
    """
    if state not in STATES:
        state = "idle"
    coverage = _coverage(size, state, scale=1.0 if template else _MARK_SCALE,
                         bold=template)
    pixels: List[RGBA] = []
    if template:
        for value in coverage:
            alpha = max(0.0, min(1.0, value))
            pixels.append((0, 0, 0, int(round(255 * alpha))))
        return encode_png(size, size, pixels)

    # The app icon sits on the macOS grid: a superellipse ("squircle") with
    # the bezel padding the platform expects, not a bare circle.
    half = _SQUIRCLE_HALF
    exponent = _SQUIRCLE_EXPONENT
    samples = 3
    step = 1.0 / samples
    weight = 1.0 / (samples * samples)
    for py in range(size):
        for px in range(size):
            inside = 0.0
            for sy in range(samples):
                for sx in range(samples):
                    x = (px + (sx + 0.5) * step) / size - 0.5
                    y = (py + (sy + 0.5) * step) / size - 0.5
                    if (abs(x / half) ** exponent + abs(y / half) ** exponent) <= 1.0:
                        inside += weight
            base = _blend((0, 0, 0, 0), ground, inside)
            pixels.append(_blend(base, mark, max(0.0, min(1.0, coverage[py * size + px]))))
    return encode_png(size, size, pixels)


# -- files ----------------------------------------------------------------

#: The macOS .icns ladder, as ``iconutil`` insists on seeing it: the exact
#: ten filenames it recognises, and nothing else in the folder.
ICONSET = (
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
)
ICNS_SIZES = tuple(sorted({size for _name, size in ICONSET}))
#: The Windows .ico ladder.
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def write_app_icons(directory: Path) -> List[Path]:
    """The app icon ladder (iconset layout, ready for ``iconutil``)."""
    iconset = Path(directory) / "icon.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    # Anything unexpected in here makes iconutil refuse the whole set, so a
    # stale file from an older ladder would break the build.
    for stale in iconset.glob("*.png"):
        stale.unlink()

    written: List[Path] = []
    cache: Dict[int, bytes] = {}
    for name, size in ICONSET:
        data = cache.setdefault(size, render(size, "idle"))
        path = iconset / name
        path.write_bytes(data)
        written.append(path)
    master = Path(directory) / "icon-1024.png"
    master.write_bytes(cache[1024])
    written.append(master)
    return written


def write_tray_icons(directory: Path) -> Dict[str, Path]:
    """The four tray states as template images, @1x and @2x."""
    tray = Path(directory) / "tray"
    tray.mkdir(parents=True, exist_ok=True)
    out: Dict[str, Path] = {}
    for state in STATES:
        for size, suffix in ((22, ""), (44, "@2x")):
            path = tray / f"{state}{suffix}.png"
            path.write_bytes(render(size, state, template=True))
            out[f"{state}{suffix}"] = path
    return out


def write_ico(path: Path, sizes: Iterable[int] = ICO_SIZES) -> Path:
    """A Windows .ico containing PNG-compressed entries."""
    images = [(size, render(size, "idle")) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, payload = b"", b""
    for size, data in images:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32,
                               len(data), offset)
        payload += data
        offset += len(data)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + payload)
    return path


def tray_icon_bytes(state: str, size: int = 22) -> bytes:
    return render(size, state, template=True)

#!/usr/bin/env python3
"""Generate the icon ladder from code (no binary assets in the repository).

    python tools/make_icons.py [--out assets]

Produces:
    assets/icon.iconset/…      the macOS ladder, ready for `iconutil`
    assets/icon-1024.png       the master
    assets/icon.ico            the Windows ladder
    assets/tray/*.png          the four tray states as template images

``build/build_macos.sh`` runs this and then `iconutil -c icns`.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bgassist.ui import icons  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="assets")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    written = icons.write_app_icons(out)
    tray = icons.write_tray_icons(out)
    ico = icons.write_ico(out / "icon.ico")
    print(f"{len(written)} app icons -> {out / 'icon.iconset'}")
    print(f"{len(tray)} tray images -> {out / 'tray'}")
    print(f"windows icon        -> {ico}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Crop logo-brand.png → logo.png (square, centered on the icon)

Usage:
  1. Save the brand image as  static/logo-brand.png
  2. Run:  python scripts/crop_logo.py
  Output: static/logo.png  (512 × 512 transparent-ready PNG)
"""
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("Pillow not installed — run:  pip install Pillow")
    sys.exit(1)

ROOT   = Path(__file__).parent.parent
SRC    = ROOT / "static" / "logo-brand.png"
DST    = ROOT / "static" / "logo.png"
SIZE   = 512   # output square size (px)

if not SRC.exists():
    print(f"❌  Source not found: {SRC}")
    print("    Save the brand image as static/logo-brand.png first.")
    sys.exit(1)

img = Image.open(SRC).convert("RGBA")
w, h = img.size
print(f"Source: {w}×{h}")

# Crop a centred square (the icon lives in the middle of the wide image)
side   = min(w, h)
left   = (w - side) // 2
top    = (h - side) // 2
square = img.crop((left, top, left + side, top + side))

# Resize to target size
out = square.resize((SIZE, SIZE), Image.LANCZOS)
out.save(DST, "PNG", optimize=True)
print(f"✅  Saved {DST}  ({SIZE}×{SIZE})")

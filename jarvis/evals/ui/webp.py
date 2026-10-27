"""PNG -> WebP for the committed screenshots: python webp.py IN.png OUT.webp  (lossy, quality 62: text stays readable, about 15-40 KB each)."""
import sys

from PIL import Image

Image.open(sys.argv[1]).convert("RGB").save(sys.argv[2], "WEBP", quality=62, method=6)

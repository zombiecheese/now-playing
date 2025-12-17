
# src/services/backdrop.py
from typing import Tuple
from PIL import Image, ImageFilter

def make_backdrop(artist_img: Image.Image, canvas_size: Tuple[int, int]) -> Image.Image:
    """
    Prepare a soft, readable photo backdrop:
      - resize to cover canvas
      - Gaussian blur
      - semi-transparent dark overlay
    Returns RGBA image sized to canvas.
    """
    w, h = canvas_size
    bg = artist_img.copy().resize((w, h), Image.LANCZOS)

    # Blur to push detail into background (Pillow GaussianBlur)
    # Ref: standard Pillow blur usage for readability under text/icons. [1](https://forums.pimoroni.com/t/rotate-image-on-inky-impression-7-3/22857)
    bg = bg.filter(ImageFilter.GaussianBlur(radius=12))

    # Darken slightly for text legibility using alpha composite
    # Ref: Pillow alpha compositing/blending patterns. [2](https://pythonexamples.org/python-pillow-rotate-image-90-180-270-degrees/)
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 120))  # tune alpha 80–150
    bg = Image.alpha_composite(bg, overlay)

   

"""Draw the EshanAI application icon (dark navy rounded square, epsilon
monogram, presence dot) and emit a multi-size .ico + PNG previews.
Pillow only — no new dependencies."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
ASSETS.mkdir(exist_ok=True)

S = 256

PALETTE = {
    "bg": "#0D1420",
    "bg_hi": "#151E2B",
    "border": "#273548",
    "glyph": "#F1F5F9",
    "accent": "#4C83C3",
    "accent2": "#78A9DF",
}


def rounded(draw, xy, radius, fill, outline=None, width=0):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# background plate
rounded(d, (6, 6, S - 6, S - 6), 52, PALETTE["bg"], PALETTE["border"], 3)
# a subtle inner sheen (top band, very low alpha)
sheen = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ds = ImageDraw.Draw(sheen)
rounded(ds, (10, 10, S - 10, S // 2 + 6), 48, "#1B2736", "#00000000")
from PIL import Image as _I
sheen = sheen.split()[3].point(lambda a: int(a * 0.35)).convert("L")
sheen_img = _I.new("RGBA", (S, S), (27, 39, 54, 90))
sheen_img.putalpha(sheen)
img = _I.alpha_composite(img, sheen_img)
d = ImageDraw.Draw(img)

# epsilon monogram
font = None
for name in ("seguisb.ttf", "segoeuib.ttf", "segoeui.ttf"):
    try:
        font = ImageFont.truetype(f"C:/Windows/Fonts/{name}", 168)
        break
    except Exception:
        continue
if font is None:
    font = ImageFont.load_default()

glyph = "\u03B5"          # epsilon
bbox = d.textbbox((0, 0), glyph, font=font)
gw, gh = bbox[2] - bbox[0], bbox[3] - bbox[1]
gx, gy = (S - gw) // 2 - bbox[0], (S - gh) // 2 - bbox[1] + 6
# soft accent shadow behind the glyph
d.text((gx + 3, gy + 5), glyph, font=font, fill="#2A3B52")
d.text((gx, gy), glyph, font=font, fill=PALETTE["glyph"])

# presence dot (bottom-right, like a chat presence indicator)
dot_r = 20
dot_c = (S - 52, S - 52)
d.ellipse((dot_c[0] - dot_r, dot_c[1] - dot_r, dot_c[0] + dot_r, dot_c[1] + dot_r),
          fill=PALETTE["accent"], outline=PALETTE["accent2"], width=3)

img.save(ASSETS / "eshanai_256.png")

# multi-size ico (from the 256 master, LANCZOS resamples)
sizes = [16, 24, 32, 48, 64, 128, 256]
img.save(ASSETS / "eshanai.ico", sizes=[(s, s) for s in sizes],
         append_images=[img.resize((s, s), Image.LANCZOS) for s in sizes[1:]])

for s in (48, 32, 16):
    img.resize((s, s), Image.LANCZOS).save(ASSETS / f"preview_{s}.png")
print("icon written:", ASSETS / "eshanai.ico")

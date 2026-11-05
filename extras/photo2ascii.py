#!/usr/bin/env python3
"""Turn a photo into cartoon-style ASCII art: blank skin, inked features.

Needs Pillow and NumPy (sudo apt install python3-pil python3-numpy). Results
depend heavily on the photo, so tune it by eye. --preview draws the text at
the printer's proportions, which saves paper.
"""
import argparse, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
CHAR_ASPECT = 0.6    # 10 characters per inch, 6 lines per inch
S = 4                # edge and tone samples per character cell


def ints(text):
    return tuple(int(v) for v in text.split(","))


def parse_args():
    p = argparse.ArgumentParser(description="Convert a photo to cartoon-style ASCII art on stdout.")
    p.add_argument("photo")
    p.add_argument("--width", type=int, default=80, help="columns (default: %(default)s)")
    p.add_argument("--crop", type=ints, metavar="X0,Y0,X1,Y1", help="region of the photo to use, in pixels")
    p.add_argument("--outline", metavar="FILE", help='subject outline, "x,y" per line in photo pixels; everything outside it is blanked')
    p.add_argument("--solid-above", type=int, metavar="Y", help="only fill dark areas solid above this photo row, e.g. the hairline region (default: whole image)")
    p.add_argument("--dark", type=float, default=0.30, help="tone below which an area is filled solid (default: %(default)s)")
    p.add_argument("--k1", type=float, default=0.06, help="how much darker than its surroundings gets light ink (default: %(default)s)")
    p.add_argument("--k2", type=float, default=0.10, help="how much darker gets heavy ink (default: %(default)s)")
    p.add_argument("--preview", metavar="PNG", help="also save a picture of the result")
    return p.parse_args()


def main():
    args = parse_args()
    src = Image.open(args.photo).convert("L")
    crop = args.crop or (0, 0, src.width, src.height)
    if args.outline:
        with open(args.outline) as f:
            poly = [ints(l.strip()) for l in f if l.strip()]
        m = Image.new("L", src.size, 0)
        ImageDraw.Draw(m).polygon(poly, fill=255)
    else:
        m = Image.new("L", src.size, 255)
    m = m.crop(crop)
    g = src.crop(crop)
    W = args.width
    H = int(W * g.height / g.width * CHAR_ASPECT)
    size = (W * S, H * S)
    solid_rows = H if args.solid_above is None else int((args.solid_above - crop[1]) / (crop[3] - crop[1]) * H)

    def arr(im):
        return np.asarray(im.resize(size, Image.LANCZOS), dtype=float) / 255

    a = np.asarray(g, dtype=float)
    lo, hi = np.percentile(a[np.asarray(m) > 127], [2, 97])
    norm = Image.fromarray((np.clip((a - lo) / (hi - lo), 0, 1) * 255).astype(np.uint8))
    dog = arr(norm.filter(ImageFilter.GaussianBlur(3))) - arr(norm.filter(ImageFilter.GaussianBlur(30)))  # negative = darker than surroundings
    tone = arr(norm.filter(ImageFilter.MedianFilter(9)))

    def pool(x, f):
        return f(x.reshape(H, S, W, S), axis=(1, 3))

    d = pool(dog, np.min) * 0.5 + pool(dog, np.mean) * 0.5
    t = pool(tone, np.mean)
    mb = np.asarray(m.resize(size, Image.BILINEAR)) > 127
    inside = pool(mb, np.all)
    edge = pool(mb, np.any) & ~inside

    out = []
    for y in range(H):
        row = ""
        for x in range(W):
            if edge[y, x]:                                row += "#"
            elif not inside[y, x]:                        row += " "
            elif t[y, x] < args.dark and y < solid_rows:  row += "@" if t[y, x] < args.dark * 0.6 else "#"
            elif d[y, x] < -args.k2 * 1.6:                row += "#"
            elif d[y, x] < -args.k2:                      row += "*"
            elif d[y, x] < -args.k1:                      row += ":"
            elif d[y, x] < -args.k1 * 0.5:                row += "."
            else:                                         row += " "
        out.append(row.rstrip())
    print("\n".join(out))
    print("%d columns x %d lines" % (W, H), file=sys.stderr)

    if args.preview:
        f = ImageFont.truetype(FONT, 16)
        cw = f.getlength("M")
        lh = int(cw / CHAR_ASPECT)
        img = Image.new("L", (int(cw * W) + 20, lh * H + 20), 255)
        dr = ImageDraw.Draw(img)
        for i, l in enumerate(out):
            dr.text((10, 10 + i * lh), l, font=f, fill=0)
        img.save(args.preview)


if __name__ == "__main__":
    main()

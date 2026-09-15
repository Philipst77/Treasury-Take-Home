"""Generate synthetic test labels plus a manifest of application data.

Run:  python scripts/generate_samples.py

Each sample is designed to exercise one rule. `manifest.csv` holds the
"application" values an agent would type in, and doubles as an example
batch file for the "Check many labels" screen.
"""

from __future__ import annotations

import csv
import random
import textwrap
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent.parent / "samples"
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")

WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not "
    "drink alcoholic beverages during pregnancy because of the risk of birth defects. "
    "(2) Consumption of alcoholic beverages impairs your ability to drive a car or "
    "operate machinery, and may cause health problems."
)


def font(size: int, bold: bool = False, serif: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSerif" if serif else "DejaVuSans"
    name += "-Bold.ttf" if bold else ".ttf"
    return ImageFont.truetype(str(FONT_DIR / name), size)


def centered(draw: ImageDraw.ImageDraw, y: int, text: str, fnt, fill, width: int) -> int:
    box = draw.textbbox((0, 0), text, font=fnt)
    draw.text(((width - (box[2] - box[0])) / 2, y), text, font=fnt, fill=fill)
    return y + (box[3] - box[1]) + int(fnt.size * 0.55)


def draw_warning(draw, x, y, text, width_chars, fill, size=19):
    """Bold capitalized header, regular body, wrapped."""
    lines = textwrap.wrap(text, width_chars)
    bold, regular = font(size, bold=True), font(size)
    for i, line in enumerate(lines):
        if i == 0 and line.upper().startswith("GOVERNMENT WARNING"):
            head = line[: len("GOVERNMENT WARNING:")]
            draw.text((x, y), head, font=bold, fill=fill)
            hw = draw.textlength(head + " ", font=bold)
            draw.text((x + hw, y), line[len(head):].lstrip(), font=regular, fill=fill)
        else:
            draw.text((x, y), line, font=regular, fill=fill)
        y += int(size * 1.4)
    return y


def make_label(spec: dict) -> Image.Image:
    w, h = 1000, 1300
    bg, ink, accent = spec.get("colors", ("#f3ecdc", "#2b1d12", "#7a2e1c"))
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    d.rectangle([24, 24, w - 24, h - 24], outline=accent, width=6)
    d.rectangle([40, 40, w - 40, h - 40], outline=accent, width=2)

    y = 110
    size = 76
    while d.textlength(spec["brand"], font=font(size, bold=True, serif=True)) > w - 200:
        size -= 2  # keep the title clear of the border
    y = centered(d, y, spec["brand"], font(size, bold=True, serif=True), ink, w)
    d.line([200, y, w - 200, y], fill=accent, width=3)
    y += 40
    for line in textwrap.wrap(spec["class_type"], 26):
        y = centered(d, y, line, font(44, serif=True), accent, w)
    y += 40
    y = centered(d, y, spec["abv"], font(40, bold=True), ink, w)
    y = centered(d, y, spec["net"], font(40, bold=True), ink, w)
    y += 30
    for line in spec.get("extra", []):
        y = centered(d, y, line, font(26), ink, w)
    if spec.get("warning") and not spec.get("sideways"):
        draw_warning(d, 80, h - 300, spec["warning"], 72, ink)

    if spec.get("sideways"):
        # Put the warning up the right edge, rotated 90 degrees.
        strip = Image.new("RGB", (h - 120, 170), bg)
        draw_warning(ImageDraw.Draw(strip), 10, 10, spec["warning"], 100, ink, size=19)
        strip = strip.rotate(90, expand=True)
        img = img.resize((w, h))
        wide = Image.new("RGB", (w + 190, h), bg)
        wide.paste(img, (0, 0))
        wide.paste(strip, (w + 10, 60))
        img = wide
    return img


def photo_effects(img: Image.Image, angle: float, seed: int = 7) -> Image.Image:
    """Simulate a phone photo: tilt, glare, uneven light, blur, noise."""
    rnd = np.random.default_rng(seed)
    canvas = Image.new("RGB", (img.width + 300, img.height + 300), "#5b5f63")
    canvas.paste(img, (150, 150))
    canvas = canvas.rotate(angle, resample=Image.BICUBIC, fillcolor="#5b5f63")

    arr = np.asarray(canvas).astype(np.float32)
    hgt, wid = arr.shape[:2]
    yy, xx = np.mgrid[0:hgt, 0:wid]
    shade = 0.75 + 0.3 * (xx / wid)  # darker on the left
    glare = 70 * np.exp(-(((xx - wid * 0.72) ** 2) / (2 * 120 ** 2) + ((yy - hgt * 0.35) ** 2) / (2 * 260 ** 2)))
    arr = arr * shade[..., None] + glare[..., None]
    arr += rnd.normal(0, 7, arr.shape)
    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    return out.filter(ImageFilter.GaussianBlur(0.9))


OLD_TOM = dict(
    brand="OLD TOM DISTILLERY",
    class_type="Kentucky Straight Bourbon Whiskey",
    abv="45% Alc./Vol. (90 Proof)",
    net="750 mL",
    extra=["Distilled and bottled by Old Tom Distillery", "Bardstown, Kentucky"],
    warning=WARNING,
)
OLD_TOM_APP = dict(
    brand_name="OLD TOM DISTILLERY",
    class_type="Kentucky Straight Bourbon Whiskey",
    alcohol_content="45% Alc./Vol. (90 Proof)",
    net_contents="750 mL",
    bottler="Old Tom Distillery, Bardstown, Kentucky",
    country_of_origin="",
)

SAMPLES = [
    ("01_old_tom_correct.png", "Correct label; everything matches", "pass",
     OLD_TOM, OLD_TOM_APP),
    ("02_stones_throw_capitalization.png", "Label in capitals, application in title case (should still match)", "pass",
     dict(OLD_TOM, brand="STONE'S THROW", class_type="Small Batch Gin", abv="41.5% Alc./Vol. (83 Proof)",
          extra=["Distilled by Stone's Throw Spirits Co.", "Portland, Oregon"], colors=("#e6eef0", "#10303a", "#1f5f6e")),
     dict(OLD_TOM_APP, brand_name="Stone's Throw", class_type="small batch gin",
          alcohol_content="41.5% Alc./Vol. (83 Proof)", bottler="Stone's Throw Spirits Co., Portland, Oregon")),
    ("03_wrong_abv.png", "Label says 40%, application says 45%", "fail",
     dict(OLD_TOM, abv="40% Alc./Vol. (80 Proof)"), OLD_TOM_APP),
    ("04_titlecase_warning.png", "Warning heading is not in capitals", "fail",
     dict(OLD_TOM, warning=WARNING.replace("GOVERNMENT WARNING:", "Government Warning:")), OLD_TOM_APP),
    ("05_reworded_warning.png", "Warning wording was changed", "fail",
     dict(OLD_TOM, warning=WARNING.replace("may cause health problems", "might cause some health issues")), OLD_TOM_APP),
    ("06_wrong_net_contents.png", "Label says 1 L, application says 750 mL", "fail",
     dict(OLD_TOM, net="1 L"), OLD_TOM_APP),
    ("07_missing_warning.png", "No government warning on the label", "fail",
     dict(OLD_TOM, warning=None), OLD_TOM_APP),
    ("08_imported_wine_us_units.png", "Imported wine; 25.4 FL. OZ. on label equals 750 mL", "pass",
     dict(brand="CASTELLO DI VERNA", class_type="Chianti Classico Red Wine", abv="13.5% Alc./Vol.",
          net="25.4 FL. OZ.", extra=["Product of Italy", "Imported by Vine Road Imports, Newark, NJ"],
          warning=WARNING, colors=("#f4eef2", "#3a1026", "#6e1f45")),
     dict(brand_name="Castello di Verna", class_type="Chianti Classico Red Wine", alcohol_content="13.5%",
          net_contents="750 mL", bottler="Vine Road Imports, Newark, NJ", country_of_origin="Italy")),
    ("09_sideways_warning.png", "Warning printed up the side of the label", "pass",
     dict(brand="HARBOR LIGHT", class_type="India Pale Ale", abv="6.8% Alc./Vol.", net="12 FL OZ",
          extra=["Brewed by Harbor Light Brewing", "Seattle, Washington"], warning=WARNING, sideways=True,
          colors=("#eef1f6", "#14213d", "#2d4b8a")),
     dict(brand_name="Harbor Light", class_type="India Pale Ale", alcohol_content="6.8% ABV",
          net_contents="12 fl oz", bottler="Harbor Light Brewing, Seattle, Washington", country_of_origin="")),
]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    rows = []
    for filename, desc, expect, spec, app in SAMPLES:
        make_label(spec).save(OUT / filename)
        rows.append(dict(filename=filename, description=desc, expected_result=expect, **app))

    photo = photo_effects(make_label(OLD_TOM), angle=6.5)
    photo.save(OUT / "10_tilted_photo_with_glare.jpg", quality=80)
    rows.append(dict(filename="10_tilted_photo_with_glare.jpg",
                     description="Phone photo: tilted, glare, uneven light", expected_result="pass",
                     **OLD_TOM_APP))

    with (OUT / "manifest.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} samples to {OUT}")


if __name__ == "__main__":
    random.seed(1)
    main()

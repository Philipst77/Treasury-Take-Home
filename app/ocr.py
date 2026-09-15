"""Read text from a label image with local Tesseract OCR.

Why local OCR instead of a cloud vision API: the agency network blocks
outbound traffic to many domains (Marcus), and a previous vendor pilot
failed for exactly that reason. Tesseract runs entirely on the server,
needs no API keys, and sends label images nowhere.

Photos are rarely perfect (Jenny), so each image is cleaned up first:
orientation from EXIF, resizing, contrast equalization for glare and
dim lighting, and small-angle deskewing. Two OCR passes with different
page-layout assumptions run in parallel; the matcher then uses whichever
pass read each field best. If no health warning is found, rotated passes
are tried because warnings are often printed sideways.
"""

from __future__ import annotations

import io
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

# Tesseract's internal OpenMP threads fight with our parallel passes;
# single-threaded Tesseract is ~2x faster here. Must be set before use.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pytesseract  # noqa: E402
from PIL import Image, ImageOps, UnidentifiedImageError

# Tesseract per-call timeout (seconds). Keeps one bad image from blocking.
OCR_TIMEOUT = float(os.getenv("OCR_TIMEOUT", "8"))
TARGET_LONG_SIDE = 2000
MIN_LONG_SIDE = 1200

_pool = ThreadPoolExecutor(max_workers=int(os.getenv("OCR_THREADS", "4")))


class ImageError(ValueError):
    """The upload isn't a readable image."""


@dataclass
class OcrResult:
    texts: list[str] = field(default_factory=list)  # one per OCR pass
    seconds: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def combined(self) -> str:
        return "\n".join(self.texts)


def load_image(data: bytes) -> np.ndarray:
    """Decode bytes into an upright grayscale array."""
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)  # phone photos store rotation in EXIF
        img = img.convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise ImageError("This file isn't an image we can read. Upload a JPG, PNG, or WEBP.") from exc
    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2GRAY)


def _resize(gray: np.ndarray) -> np.ndarray:
    h, w = gray.shape
    long_side = max(h, w)
    if long_side > TARGET_LONG_SIDE:
        scale = TARGET_LONG_SIDE / long_side
    elif long_side < MIN_LONG_SIDE:
        scale = MIN_LONG_SIDE / long_side  # small text reads better enlarged
    else:
        return gray
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    return cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=interp)


def _estimate_skew(gray: np.ndarray) -> float:
    """Find the small rotation (±12°) that makes text rows line up best.

    Projection-profile method on a downscaled binary image: the right
    angle gives the sharpest alternation between text rows and gaps.
    """
    small = cv2.resize(gray, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
    binary = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    if binary.mean() > 127:  # light text on dark background
        binary = 255 - binary
    h, w = binary.shape
    center = (w / 2, h / 2)

    def score(angle: float) -> float:
        m = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(binary, m, (w, h), flags=cv2.INTER_NEAREST)
        profile = rotated.sum(axis=1, dtype=np.float64)
        return float(np.sum(np.diff(profile) ** 2))

    coarse = max(np.arange(-12, 12.1, 2), key=score)
    fine = max(np.arange(coarse - 2, coarse + 2.1, 0.5), key=score)
    return float(fine)


def _rotate(gray: np.ndarray, angle: float) -> np.ndarray:
    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def preprocess(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Return (enhanced grayscale, binarized) images plus notes."""
    notes = []
    gray = _resize(gray)

    angle = _estimate_skew(gray)
    if abs(angle) >= 1.0:
        gray = _rotate(gray, angle)
        notes.append(f"Straightened a {abs(angle):.1f}\u00b0 tilt.")

    # CLAHE evens out glare and shadows locally rather than globally.
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    enhanced = cv2.fastNlMeansDenoising(enhanced, None, h=7, templateWindowSize=7, searchWindowSize=15)

    binary = cv2.adaptiveThreshold(
        enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15
    )
    return enhanced, binary, notes


def _tesseract(img: np.ndarray, psm: int) -> str:
    try:
        return pytesseract.image_to_string(
            img, lang="eng", config=f"--oem 1 --psm {psm}", timeout=OCR_TIMEOUT
        )
    except RuntimeError:  # pytesseract raises RuntimeError on timeout
        return ""


def _has_warning(text: str) -> bool:
    return re.search(r"government\s*warning|surgeon\s*general", text, re.IGNORECASE) is not None


def read_label(data: bytes) -> OcrResult:
    start = time.perf_counter()
    gray = load_image(data)
    enhanced, binary, notes = preprocess(gray)

    # psm 3: automatic layout (good for blocks like the warning).
    # psm 11: sparse text (good for scattered label elements).
    passes = [_pool.submit(_tesseract, enhanced, 3), _pool.submit(_tesseract, binary, 11)]
    texts = [p.result() for p in passes]

    if not any(_has_warning(t) for t in texts):
        # Warnings are often printed vertically along the label edge.
        rotated = [
            _pool.submit(_tesseract, cv2.rotate(enhanced, code), 6)
            for code in (cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE)
        ]
        extra = [r.result() for r in rotated]
        if any(_has_warning(t) for t in extra):
            notes.append("Read sideways text.")
        texts.extend(extra)

    result = OcrResult(texts=[t for t in texts if t.strip()], notes=notes)
    if sum(len(re.findall(r"[A-Za-z]", t)) for t in result.texts) < 20:
        result.notes.append("Very little text could be read. Try a sharper, closer, evenly lit photo.")
    result.seconds = round(time.perf_counter() - start, 2)
    return result

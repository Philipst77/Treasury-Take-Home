"""HTTP API and static UI for the label verification prototype."""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .matching import FAIL, PASS, REVIEW, verify_label
from .ocr import ImageError, read_label

log = logging.getLogger("label-verifier")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "app" / "static"
SAMPLES_DIR = ROOT / "samples"

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/tiff", "image/bmp"}
_RANK = {PASS: 2, REVIEW: 1, FAIL: 0}

app = FastAPI(title="Label Verification Prototype", docs_url="/api/docs", redoc_url=None)


def _best_of(candidates: list[dict]) -> dict:
    """Merge results from several OCR passes, keeping the best read per field.

    Each pass misreads different things; a field that one pass reads
    cleanly shouldn't fail because another pass garbled it.
    """
    merged: dict[str, dict] = {}
    for cand in candidates:
        for f in cand["fields"]:
            cur = merged.get(f["field"])
            better = cur is None or _RANK[f["status"]] > _RANK[cur["status"]] or (
                _RANK[f["status"]] == _RANK[cur["status"]] and len(f["found"]) > len(cur["found"])
                and f["status"] != PASS
            )
            if better:
                merged[f["field"]] = f
    fields = list(merged.values())
    statuses = {f["status"] for f in fields}
    overall = FAIL if FAIL in statuses else REVIEW if REVIEW in statuses else PASS
    return {"overall": overall, "fields": fields}


def verify_image(data: bytes, application: dict[str, str]) -> dict:
    ocr = read_label(data)
    if not ocr.texts:
        return {
            "overall": FAIL,
            "fields": [],
            "notes": ocr.notes or ["No text could be read from this image."],
            "seconds": ocr.seconds,
            "ocr_text": "",
        }
    candidates = [verify_label(application, t) for t in ocr.texts]
    candidates.append(verify_label(application, ocr.combined))
    result = _best_of(candidates)
    result.update(notes=ocr.notes, seconds=ocr.seconds, ocr_text=ocr.texts[0])
    return result


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/verify")
async def verify(
    image: UploadFile = File(...),
    brand_name: str = Form(""),
    class_type: str = Form(""),
    alcohol_content: str = Form(""),
    net_contents: str = Form(""),
    bottler: str = Form(""),
    country_of_origin: str = Form(""),
    check_warning: str = Form("true"),
) -> dict:
    if image.content_type and image.content_type not in ALLOWED_TYPES:
        raise HTTPException(415, "Upload a JPG, PNG, or WEBP image of the label.")
    data = await image.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "This image is larger than 15 MB. Upload a smaller photo.")
    if not data:
        raise HTTPException(400, "The uploaded file is empty.")

    application = {
        "brand_name": brand_name,
        "class_type": class_type,
        "alcohol_content": alcohol_content,
        "net_contents": net_contents,
        "bottler": bottler,
        "country_of_origin": country_of_origin,
        "check_warning": check_warning,
    }
    has_fields = any(v.strip() for k, v in application.items() if k != "check_warning")
    if not has_fields and check_warning.lower() == "false":
        raise HTTPException(400, "Enter at least one value from the application to compare.")

    try:
        result = await run_in_threadpool(verify_image, data, application)
    except ImageError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception:  # never leak internals to the page
        log.exception("Verification failed for %s", image.filename)
        raise HTTPException(500, "Something went wrong reading this label. Try again or use a different photo.")

    log.info("verified %s -> %s in %.2fs", image.filename, result["overall"], result["seconds"])
    result["filename"] = image.filename
    return result


@app.get("/api/samples")
def samples() -> list[dict]:
    """Sample labels with their application data, for quick testing."""
    manifest = SAMPLES_DIR / "manifest.csv"
    if not manifest.exists():
        return []
    with manifest.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return [{**r, "url": f"/samples/{r['filename']}"} for r in rows]


if SAMPLES_DIR.exists():
    app.mount("/samples", StaticFiles(directory=SAMPLES_DIR), name="samples")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")

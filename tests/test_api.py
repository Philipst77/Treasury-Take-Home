"""End-to-end tests through the HTTP API, using real OCR on the samples.

Requires Tesseract to be installed. Regenerate samples with
`python scripts/generate_samples.py` if they are missing.
"""

import csv
import io
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
FIELDS = ["brand_name", "class_type", "alcohol_content", "net_contents", "bottler", "country_of_origin"]

pytestmark = pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract not installed")
client = TestClient(app)


def load_manifest():
    with (SAMPLES / "manifest.csv").open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def post(path: Path, data: dict, content_type="image/png"):
    with path.open("rb") as fh:
        return client.post("/api/verify", files={"image": (path.name, fh, content_type)}, data=data)


@pytest.mark.parametrize("row", load_manifest(), ids=lambda r: r["filename"])
def test_sample_labels(row):
    ctype = "image/jpeg" if row["filename"].endswith(".jpg") else "image/png"
    res = post(SAMPLES / row["filename"], {f: row[f] for f in FIELDS}, ctype)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["overall"] == row["expected_result"], body["fields"]
    assert body["seconds"] < 10


def test_health():
    assert client.get("/api/health").json() == {"status": "ok"}


def test_index_served():
    res = client.get("/")
    assert res.status_code == 200
    assert "Label check" in res.text


def test_samples_listed():
    items = client.get("/api/samples").json()
    assert len(items) >= 10
    assert client.get(items[0]["url"]).status_code == 200


def test_rejects_non_image_type():
    res = client.post("/api/verify", files={"image": ("a.txt", b"hello", "text/plain")}, data={"brand_name": "x"})
    assert res.status_code == 415


def test_rejects_corrupt_image():
    res = client.post("/api/verify", files={"image": ("a.png", b"not really a png", "image/png")},
                      data={"brand_name": "x"})
    assert res.status_code == 400
    assert "image" in res.json()["detail"]


def test_rejects_empty_file():
    res = client.post("/api/verify", files={"image": ("a.png", b"", "image/png")}, data={"brand_name": "x"})
    assert res.status_code == 400


def test_requires_something_to_check():
    res = post(SAMPLES / "01_old_tom_correct.png", {"check_warning": "false"})
    assert res.status_code == 400


def test_warning_only_check_allowed():
    res = post(SAMPLES / "01_old_tom_correct.png", {})
    assert res.status_code == 200
    assert [f["field"] for f in res.json()["fields"]] == ["government_warning"]


def test_blank_image_fails_gracefully():
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(buf, "PNG")
    res = client.post("/api/verify", files={"image": ("blank.png", buf.getvalue(), "image/png")},
                      data={"brand_name": "OLD TOM"})
    assert res.status_code == 200
    body = res.json()
    assert body["overall"] == "fail"
    assert body["notes"]

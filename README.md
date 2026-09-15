# Label Check: AI-assisted alcohol label verification

A prototype that compares an alcohol label image against the values in its COLA application and reports, field by field, whether they match. It automates the routine "does the number on the form equal the number on the label" work so agents can spend their time on the judgment calls.

**Live app:** `<your deployed URL>`

Open the app, pick a sample from **"Or try a sample label"**, and select **Check label**. To test batch processing, open **Check many labels** and select **Load the sample labels**, then **Check all labels**.

---

## What it does

| Check | Rule |
|---|---|
| Brand name, class/type, bottler, country of origin | Found on the label, ignoring capitalization and punctuation (`STONE'S THROW` = `Stone's Throw`). Near-matches go to review. |
| Alcohol content | Compares the number, not the text: `45%`, `45% ABV`, and `45% Alc./Vol. (90 Proof)` are equivalent. Checks proof against the application and confirms proof = 2 × ABV. |
| Net contents | Converts units: `750 mL` = `75 cl` = `25.4 FL. OZ.` (1% tolerance for label rounding). |
| Government warning | Strict. `GOVERNMENT WARNING:` must be in capitals with a colon, and the statement must match 27 CFR 16.21 word for word. Differences are listed ("'issues' instead of 'problems'"). |

Every field gets one of three results:

- **Matches.** The label clearly agrees with the application.
- **Needs review.** The label probably agrees, but something (usually scanning noise) means a person should look.
- **Doesn't match.** The value is different or missing.

Results always show the application value next to what was read from the label, so an agent can confirm the verdict at a glance.

**Batch mode** takes many images plus a CSV of application data (one row per image, matched by file name). It checks the labels in parallel with a progress bar, lists problems first, lets you filter by result, and exports a results CSV. Images without a CSV row get the warning check only.

---

## Run locally

Requirements: Python 3.11+ and [Tesseract OCR](https://tesseract-ocr.github.io/tessdoc/Installation.html).

```bash
# 1. Install Tesseract
brew install tesseract                      # macOS
sudo apt-get install tesseract-ocr          # Debian/Ubuntu
# Windows: install from https://github.com/UB-Mannheim/tesseract/wiki and add it to PATH

# 2. Install Python dependencies
python -m venv .venv
source .venv/bin/activate                   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

# 3. Start the app
uvicorn app.main:app --reload
```

Open http://localhost:8000. The API docs are at http://localhost:8000/api/docs.

**With Docker** (no local Tesseract needed):

```bash
docker build -t label-check .
docker run -p 8000:8000 label-check
```

**Tests:**

```bash
pytest -q          # 64 tests: matching rules plus end-to-end OCR on every sample
```

**Regenerate the sample labels:**

```bash
python scripts/generate_samples.py
```

---

## Deploy

The app is a single Docker container with no external services, API keys, or database.

**Render** (used for the live URL):

1. Push this repo to GitHub.
2. In Render, choose **New → Blueprint** and select the repo. `render.yaml` configures everything.
3. Use the **Starter** plan or above. The free plan's 0.1 CPU makes OCR take well over the 5-second target.

**Hugging Face Spaces** (free, 2 vCPU):

1. Create a new Space with the **Docker** SDK and push this repo to it.
2. Add this block to the top of the Space's README:

   ```yaml
   ---
   title: Label Check
   sdk: docker
   app_port: 8000
   ---
   ```

**Azure App Service** (matches TTB's existing cloud):

```bash
az acr build --registry <registry> --image label-check:latest .
az webapp create --resource-group <rg> --plan <plan> --name <app-name> \
  --deployment-container-image-name <registry>.azurecr.io/label-check:latest
az webapp config appsettings set --resource-group <rg> --name <app-name> --settings WEBSITES_PORT=8000
```

Optional environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `OCR_THREADS` | `4` | Parallel Tesseract passes per server process |
| `OCR_TIMEOUT` | `8` | Seconds before a single OCR pass is abandoned |
| `WEB_CONCURRENCY` | `1` | Uvicorn worker processes; set to about the CPU count |

---

## Approach

```
Image ──► preprocess ──► Tesseract (2 passes in parallel) ──► compare each field ──► merge best reads ──► result
          • EXIF rotation   • automatic layout (psm 3)          • fuzzy text
          • resize          • sparse text (psm 11)              • numeric ABV/proof
          • deskew ±12°     • +90°/−90° if no warning found     • unit-converted volume
          • CLAHE contrast                                      • word-level warning diff
          • denoise, binarize
```

1. **Preprocessing** (`app/ocr.py`). This handles the imperfect photos Jenny described. It applies phone EXIF rotation, finds and corrects tilt with a projection-profile search, uses local contrast equalization (CLAHE) to even out glare and shadows, and removes noise.
2. **OCR.** Two Tesseract passes with different page-layout assumptions run in parallel, since labels mix text blocks with scattered elements. If neither finds a health warning, two rotated passes run too, because warnings are often printed up the side of a label.
3. **Comparison** (`app/matching.py`). Each field has its own rule (table above). All checks are pure functions with no OCR dependency, so they're fully unit-tested.
4. **Merging** (`app/main.py`). Each OCR pass misreads different things, so the comparison runs on every pass and keeps the best result per field. A field that one pass reads cleanly doesn't fail because another pass garbled it.

### Tools

- **FastAPI:** a small, typed Python API with automatic docs.
- **Tesseract 5 (LSTM engine)** through `pytesseract`: local OCR.
- **OpenCV:** image cleanup.
- **RapidFuzz:** fuzzy text similarity.
- **Frontend:** plain HTML, CSS, and JavaScript with no framework, build step, or external requests.
- **pytest and GitHub Actions:** tests.

---

## Decisions and trade-offs

These decisions are tied to what came up in the discovery interviews.

**Local OCR instead of a cloud vision/LLM API.** Marcus said the agency firewall blocks many outbound domains, and the previous vendor pilot failed partly because of this. Tesseract runs inside the container, so no label image leaves the server and there's nothing for the firewall to block. The trade-off is accuracy: a vision LLM would read stylized fonts and curved bottles better and could judge things like bold type. The comparison layer takes plain text, so a cloud or Azure-hosted OCR/LLM (for example, Azure AI Vision inside TTB's FedRAMP boundary) could be added later as another OCR pass without changing the rules.

**Speed target of about 5 seconds.** Sarah said agents abandoned a tool that took 30–40 seconds. A typical label takes about 1.5–2.5 seconds on a single vCPU. Setting `OMP_THREAD_LIMIT=1` halved the time, because Tesseract's own threading competes with our parallel passes. The sideways-warning fallback adds about 1.5 seconds and only runs when no warning is found. Every OCR call has a timeout, so one bad image can't hang the page.

**Three results instead of pass/fail.** Dave's point was that label review needs judgment. The tool never silently fails a label because of scanning noise. When it's unsure, it says **Needs review** and shows both values side by side.

**Lenient on names, strict on the warning.** Dave's `STONE'S THROW` example passes. Jenny's title-case `Government Warning` fails, as does any reworded or missing phrase. OCR-level noise in the warning (a single garbled character) goes to review, not pass.

**A UI for everyone on the team.** Sarah described her 73-year-old mother as the benchmark, and half the team is over 50. The app has two tabs, one primary button per screen, 19px base text, 48px+ touch targets, and results written as words with symbols (✓ ! ✕), never color alone. It also supports keyboard navigation, visible focus, screen-reader live regions, and reduced motion. Fonts are system fonts (Public Sans if installed, otherwise Segoe UI), so the page makes no requests outside the app.

**Batch processing in the browser, one label per request.** Janet asked for batch uploads, and a 300-label upload in a single request would hit gateway timeouts and give no progress. Instead, the browser sends three labels at a time to the same endpoint the single check uses. That gives live progress, per-label error isolation (one bad file doesn't sink the batch), and one code path to test. Throughput scales with server CPUs: about 1.6 seconds per label on one vCPU, so 300 labels takes roughly 8 minutes on one vCPU and about 2 minutes on four.

**No storage.** Marcus said not to store anything sensitive. Uploads are processed in memory and discarded, and there's no database. Batch results exist only in the browser until the agent downloads them.

**Standalone, no COLA integration**, per Marcus. The API (`POST /api/verify`) is a clean seam for a future integration.

---

## Assumptions

- The agent types (or pastes) application values. There's no COLA data feed.
- Distilled spirits, wine, and beer share one set of checks. Beverage-specific rules (for example, ABV exemptions for some wines and beers) are left to the agent, who can leave a field blank to skip it.
- Only English labels, and one image per application. Front and back labels would need to be combined into one image.
- The government warning text is the one in 27 CFR 16.21. The check covers content and capitalization, not type size or placement.
- Net contents within 1% count as a match, which covers standard metric/US conversions (750 mL ≈ 25.4 fl oz).
- A value found anywhere on the label counts as present; the tool doesn't check where it is placed.

## Limitations

- **Bold type isn't verified.** OCR returns text, not font weight. The result says so, and the agent confirms visually.
- **Stylized fonts, script lettering, and curved bottles** can defeat Tesseract. These cases fail or go to review with the raw OCR text shown ("Show all text read from the label"), never a false pass on the strict checks.
- **Only small tilts are corrected** (±12°) and 90° rotations for the warning. Strong perspective distortion isn't corrected.
- **Type size and placement** (for example, minimum warning font size, same-field-of-vision rules) aren't checked.
- **Sample labels are synthetic.** Accuracy on real COLA submissions hasn't been measured. That should be the first step before any pilot.
- **No authentication.** This is a prototype; a production version would sit behind agency SSO.

## What I'd do next

1. Measure accuracy on a few hundred real, already-adjudicated COLA labels, and tune the thresholds from that data.
2. Add an optional vision-model pass (hosted inside the agency's Azure boundary) for hard images and for bold/type-size checks.
3. Add beverage-type rule sets (wine appellation and vintage, beer exemptions, sulfite declarations).
4. Highlight where each value was found by drawing boxes on the label image using Tesseract's word positions.
5. Add a server-side job queue for very large batches, so an agent can close the tab and come back.

---

## Project structure

```
app/
  main.py         HTTP API, upload validation, merging of OCR passes
  ocr.py          image preprocessing and Tesseract passes
  matching.py     comparison rules (no OCR dependency)
  static/         index.html, styles.css, app.js
samples/          10 generated test labels + manifest.csv (also a batch CSV example)
scripts/
  generate_samples.py
tests/
  test_matching.py   unit tests for every rule
  test_api.py        end-to-end tests through the API with real OCR
Dockerfile, render.yaml, .github/workflows/tests.yml
```

### Sample labels

| File | Scenario | Expected |
|---|---|---|
| `01_old_tom_correct.png` | Everything matches | Matches |
| `02_stones_throw_capitalization.png` | Label in capitals, application in title case | Matches |
| `03_wrong_abv.png` | Label 40%, application 45% | Doesn't match |
| `04_titlecase_warning.png` | "Government Warning:" not in capitals | Doesn't match |
| `05_reworded_warning.png` | Warning wording changed | Doesn't match |
| `06_wrong_net_contents.png` | Label 1 L, application 750 mL | Doesn't match |
| `07_missing_warning.png` | No warning | Doesn't match |
| `08_imported_wine_us_units.png` | 25.4 FL. OZ. vs 750 mL, country of origin | Matches |
| `09_sideways_warning.png` | Warning printed vertically | Matches |
| `10_tilted_photo_with_glare.jpg` | Phone photo: tilt, glare, uneven light | Matches |

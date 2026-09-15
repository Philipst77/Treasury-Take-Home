# Label Check
 
A prototype that checks an alcohol label image against its application data: brand name, class/type, alcohol content, net contents, and the government warning.
 

**Live app:** `https://label-check-772841493755.us-central1.run.app/`
- The live app is on Google Cloud Run, which shuts down when idle to stay free, so the first request after a quiet period can take several extra seconds. After that, checks take about a second. In production, one instance would stay running.
 
## Run it
 
Requires Python 3.11+ and Tesseract (`brew install tesseract`).
 
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m uvicorn app.main:app
```
 
Open http://localhost:8000. Or use Docker:
 
```bash
docker build -t label-check .
docker run -p 8000:8000 label-check
```
 
Run tests with `pytest -q`.
 
## Approach
 
The app cleans up the image (tilt, glare, rotation), reads the text with Tesseract OCR, and compares each field:
 
- **Names** ignore capitalization and punctuation (`STONE'S THROW` matches `Stone's Throw`).
- **Alcohol and volume** compare numbers and convert units (`750 mL` matches `25.4 FL. OZ.`).
- **The government warning** must match word for word, with `GOVERNMENT WARNING:` in capitals.
Each field is marked **Matches**, **Needs review**, or **Doesn't match**. Batch mode checks many labels at once using a CSV.
 
## Tools
 
FastAPI, Tesseract OCR, OpenCV, RapidFuzz, plain HTML/CSS/JavaScript, pytest, and Docker.
 
## Assumptions and limitations
 
- OCR runs locally, so no images leave the server and the agency firewall isn't an issue.
- Application values are entered by hand; there's no COLA integration, and nothing is stored.
- English labels only. Bold type, font size, and placement aren't checked.
- Stylized or curved text may be misread; unclear cases go to review.
- Sample labels in `samples/` are synthetic.
 
"""Compare what the application says against the text read from the label.

Every check returns a FieldResult with one of three statuses:

    PASS    - the label clearly matches the application
    REVIEW  - probably fine, but an agent should look (e.g. OCR noise)
    FAIL    - the label does not match, or the value can't be found

The three-state design follows the stakeholder interviews: agents want
the routine matching done for them, but "you need judgment" (Dave).
Anything the tool is unsure about goes to a human instead of being
silently passed or failed.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from enum import Enum

from rapidfuzz import fuzz

PASS, REVIEW, FAIL = "pass", "review", "fail"

# 27 CFR 16.21. The header must be capitalized; the rest is fixed text.
WARNING_HEADER = "GOVERNMENT WARNING:"
WARNING_BODY = (
    "(1) According to the Surgeon General, women should not drink alcoholic "
    "beverages during pregnancy because of the risk of birth defects. "
    "(2) Consumption of alcoholic beverages impairs your ability to drive a "
    "car or operate machinery, and may cause health problems."
)
STANDARD_WARNING = f"{WARNING_HEADER} {WARNING_BODY}"

# Fuzzy thresholds for free-text fields (0-100, rapidfuzz scale).
TEXT_PASS_SCORE = 92
TEXT_REVIEW_SCORE = 75


class Field(str, Enum):
    BRAND = "brand_name"
    CLASS_TYPE = "class_type"
    ALCOHOL = "alcohol_content"
    NET = "net_contents"
    BOTTLER = "bottler"
    ORIGIN = "country_of_origin"
    WARNING = "government_warning"


FIELD_LABELS = {
    Field.BRAND: "Brand name",
    Field.CLASS_TYPE: "Class / type",
    Field.ALCOHOL: "Alcohol content",
    Field.NET: "Net contents",
    Field.BOTTLER: "Bottler name and address",
    Field.ORIGIN: "Country of origin",
    Field.WARNING: "Government warning",
}


@dataclass
class FieldResult:
    field: str
    label: str
    status: str
    expected: str
    found: str
    note: str

    def to_dict(self) -> dict:
        return asdict(self)


def _result(field: Field, status: str, expected: str, found: str, note: str) -> FieldResult:
    return FieldResult(field.value, FIELD_LABELS[field], status, expected, found, note)


# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------

_QUOTE_MAP = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u2032": "'", "`": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"',
    "\u2013": "-", "\u2014": "-", "\u2212": "-",
    "|": "l",  # common OCR confusion inside words
})


def clean(text: str) -> str:
    """Unicode-normalize and straighten quotes/dashes; keeps case."""
    text = unicodedata.normalize("NFKC", text or "")
    return text.translate(_QUOTE_MAP)


def normalize(text: str) -> str:
    """Case-, punctuation- and whitespace-insensitive form for comparison.

    "STONE'S THROW" and "Stone's Throw" both become "stones throw".
    """
    text = clean(text).casefold()
    text = text.replace("'", "")
    text = text.replace("&", " and ")
    text = re.sub(r"[^\w%.]+", " ", text)  # keep % and . for numbers
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", text)  # drop non-numeric dots
    return re.sub(r"\s+", " ", text).strip()


def _span_matches(snippet: str, text: str) -> list[re.Match]:
    chars = [c for c in snippet if not c.isspace()]
    if not chars:
        return []
    pattern = r"[^\w]{0,4}".join(re.escape(c) for c in chars)
    return list(re.finditer(pattern, text, re.IGNORECASE))


def original_span(snippet: str, label_text: str, prefer: str = "") -> str:
    """Map a normalized snippet back to the label's own text (keeps case).

    When the text appears more than once, prefer (1) an occurrence that sits
    on its own line, which is usually the printed field rather than a
    mention inside other copy, then (2) one written exactly like `prefer`.
    Falls back to the normalized snippet if it can't be located.
    """
    text = clean(label_text)
    matches = _span_matches(snippet, text)
    if not matches:
        return snippet

    def whole(m: re.Match) -> str:
        return re.sub(r"\s+", " ", m.group(0))

    def on_own_line(m: re.Match) -> bool:
        before = text[:m.start()].rsplit("\n", 1)[-1]
        after = text[m.end():].split("\n", 1)[0]
        return not before.strip() and not re.sub(r"[^\w]", "", after)

    target = re.sub(r"\s+", " ", clean(prefer).strip())
    ranked = sorted(matches, key=lambda m: (not on_own_line(m), whole(m) != target))
    return whole(ranked[0])


# --------------------------------------------------------------------------
# Free-text fields (brand, class/type, bottler, origin)
# --------------------------------------------------------------------------

def best_text_match(expected: str, label_text: str) -> tuple[int, str]:
    """Return (score 0-100, best matching snippet of the label)."""
    exp = normalize(expected)
    lab = normalize(label_text)
    if not exp or not lab:
        return 0, ""
    if re.search(rf"(?<!\w){re.escape(exp)}(?!\w)", lab):
        return 100, exp

    # Slide a window of roughly the same number of words over the label.
    exp_len = len(exp.split())
    words = lab.split()
    best_score, best_snip = 0.0, ""
    for size in {max(1, exp_len - 1), exp_len, exp_len + 1}:
        for i in range(0, max(1, len(words) - size + 1)):
            snip = " ".join(words[i:i + size])
            score = fuzz.ratio(exp, snip)
            if score > best_score:
                best_score, best_snip = score, snip
    return int(round(best_score)), best_snip


def check_text_field(field: Field, expected: str, label_text: str) -> FieldResult:
    score, snippet = best_text_match(expected, label_text)
    snippet = original_span(snippet, label_text, prefer=expected) if snippet else snippet
    exact_case = re.sub(r"\s+", " ", clean(expected).strip()) == snippet

    if score == 100:
        note = "Exact match." if exact_case else "Matches (ignoring capitalization and punctuation)."
        return _result(field, PASS, expected, snippet, note)
    if score >= TEXT_PASS_SCORE:
        return _result(field, PASS, expected, snippet,
                       f"Near-exact match ({score}% similar); difference is likely a scanning artifact.")
    if score >= TEXT_REVIEW_SCORE:
        return _result(field, REVIEW, expected, snippet,
                       f"Similar text found ({score}% similar). Please compare by eye.")
    return _result(field, FAIL, expected, snippet if score >= 50 else "",
                   "Not found on the label.")


# --------------------------------------------------------------------------
# Alcohol content
# --------------------------------------------------------------------------

_NUM = r"(\d{1,3}(?:[.,]\d{1,2})?)"
_ABV_PATTERNS = [
    rf"{_NUM}\s*%\s*(?:alc|abv|alcohol|by\s*vol|vol)",  # 45% Alc./Vol.
    rf"(?:alc(?:ohol)?|abv)\.?\s*(?:/\s*vol\.?)?\s*(?:by\s*vol(?:ume)?\.?)?\s*:?\s*{_NUM}\s*%",  # Alcohol 45%
    rf"{_NUM}\s*%",  # bare percentage, lowest priority
]
_PROOF_PATTERN = rf"{_NUM}\s*(?:°\s*)?proof"


def _to_float(s: str) -> float:
    return float(s.replace(",", "."))


def parse_abv(text: str) -> float | None:
    t = clean(text).lower()
    m = re.search(_ABV_PATTERNS[0], t) or re.search(_ABV_PATTERNS[1], t) or re.search(_ABV_PATTERNS[2], t)
    if m:
        return _to_float(m.group(1))
    p = re.search(_PROOF_PATTERN, t)
    return _to_float(p.group(1)) / 2 if p else None


def parse_proof(text: str) -> float | None:
    p = re.search(_PROOF_PATTERN, clean(text).lower())
    return _to_float(p.group(1)) if p else None


def find_label_abvs(label_text: str) -> list[float]:
    """All alcohol-by-volume figures on the label, most specific first."""
    t = clean(label_text).lower()
    # OCR often reads "%" as "96" or "9%"; also try with a common fix.
    found: list[float] = []
    for pat in _ABV_PATTERNS:
        for m in re.finditer(pat, t):
            v = _to_float(m.group(1))
            if 0 < v <= 100 and v not in found:
                found.append(v)
        if found:
            break
    return found


def check_alcohol(expected: str, label_text: str) -> FieldResult:
    f = Field.ALCOHOL
    exp_abv = parse_abv(expected)
    if exp_abv is None:
        return _result(f, REVIEW, expected, "", "Couldn't read a percentage from the application value.")

    label_abvs = find_label_abvs(label_text)
    if not label_abvs:
        return _result(f, FAIL, expected, "", "No alcohol percentage found on the label.")

    shown = ", ".join(f"{v:g}%" for v in label_abvs)
    if not any(abs(v - exp_abv) < 0.001 for v in label_abvs):
        return _result(f, FAIL, expected, shown,
                       f"Label shows {shown}; application says {exp_abv:g}%.")

    exp_proof = parse_proof(expected)
    label_proof = parse_proof(label_text)
    found = f"{exp_abv:g}%" + (f" ({label_proof:g} proof)" if label_proof is not None else "")
    if exp_proof is not None and label_proof is not None and abs(exp_proof - label_proof) > 0.01:
        return _result(f, FAIL, expected, found,
                       f"Proof on label is {label_proof:g}; application says {exp_proof:g}.")
    if label_proof is not None and abs(label_proof - exp_abv * 2) > 0.5:
        return _result(f, REVIEW, expected, found,
                       f"Percentage matches, but label proof ({label_proof:g}) isn't 2 \u00d7 ABV.")
    return _result(f, PASS, expected, found, "Matches.")


# --------------------------------------------------------------------------
# Net contents
# --------------------------------------------------------------------------

_UNIT_TO_ML = {
    "ml": 1.0, "milliliter": 1.0, "millilitre": 1.0,
    "cl": 10.0, "centiliter": 10.0, "centilitre": 10.0,
    "l": 1000.0, "liter": 1000.0, "litre": 1000.0, "lt": 1000.0,
    "floz": 29.5735, "fluidounce": 29.5735, "oz": 29.5735,
    "pint": 473.176, "pt": 473.176, "quart": 946.353, "qt": 946.353,
    "gallon": 3785.41, "gal": 3785.41,
}
_NET_PATTERN = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*"
    r"(millilit(?:er|re)s?|centilit(?:er|re)s?|lit(?:er|re)s?|"
    r"fl\.?\s*oz\.?|fluid\s*ounces?|ml|cl|lt|l|oz|pints?|pt|quarts?|qt|gallons?|gal)\b",
    re.IGNORECASE,
)


def _unit_key(unit: str) -> str:
    u = re.sub(r"[\s.]", "", unit.lower())
    return u[:-1] if u.endswith("s") and u[:-1] in _UNIT_TO_ML else u


def parse_volumes_ml(text: str) -> list[tuple[float, str]]:
    out = []
    for m in _NET_PATTERN.finditer(clean(text)):
        key = _unit_key(m.group(2))
        if key in _UNIT_TO_ML:
            out.append((_to_float(m.group(1)) * _UNIT_TO_ML[key], m.group(0)))
    return out


def check_net_contents(expected: str, label_text: str) -> FieldResult:
    f = Field.NET
    exp = parse_volumes_ml(expected)
    if not exp:
        return _result(f, REVIEW, expected, "", "Couldn't read a volume from the application value.")
    exp_ml = exp[0][0]

    found = parse_volumes_ml(label_text)
    if not found:
        return _result(f, FAIL, expected, "", "No net contents found on the label.")

    for ml, raw in found:
        # 1% tolerance covers metric/US rounding, e.g. 750 mL vs 25.4 fl oz.
        if abs(ml - exp_ml) / exp_ml <= 0.01:
            return _result(f, PASS, expected, raw, "Matches.")

    shown = ", ".join(raw for _, raw in found)
    return _result(f, FAIL, expected, shown, f"Label shows {shown}; application says {exp[0][1]}.")


# --------------------------------------------------------------------------
# Government warning
# --------------------------------------------------------------------------

def _words(text: str) -> list[str]:
    return normalize(text).split()


def check_warning(label_text: str) -> FieldResult:
    """Strict check: header in capitals, body word-for-word.

    Tolerance is only granted for OCR noise (a handful of characters),
    and that case goes to REVIEW rather than PASS so a person confirms.
    """
    f = Field.WARNING
    text = re.sub(r"\s+", " ", clean(label_text))
    # Undo hyphenation across line breaks: "pregnan- cy" -> "pregnancy"
    text = re.sub(r"(\w)- (\w)", r"\1\2", text)

    header = re.search(r"government\s*warning\s*[:;.,]?", text, re.IGNORECASE)
    if not header:
        # Maybe the header is garbled but the body is there.
        score, _ = best_text_match(WARNING_BODY[:80], text)
        note = ("Warning text appears present but the 'GOVERNMENT WARNING:' heading "
                "wasn't found." if score >= TEXT_REVIEW_SCORE else "No government warning found on the label.")
        return _result(f, FAIL, STANDARD_WARNING, "", note)

    header_raw = header.group(0)
    problems: list[str] = []
    if header_raw.replace(" ", "")[:17] != "GOVERNMENTWARNING":
        problems.append(f"Heading must be in capital letters; label has \u201c{header_raw.strip()}\u201d.")
    if not header_raw.rstrip().endswith(":"):
        problems.append("Heading should be followed by a colon.")

    # Compare the body word-by-word.
    exp_words = _words(WARNING_BODY)
    tail_words = _words(text[header.end():])[: len(exp_words) + 15]
    sm = SequenceMatcher(None, exp_words, tail_words, autojunk=False)
    # Trim trailing text after the warning (other label copy).
    blocks = [b for b in sm.get_matching_blocks() if b.size]
    if blocks:
        last = blocks[-1]
        leftover = len(exp_words) - (last.a + last.size)  # expected words after the last match
        end = min(len(tail_words), last.b + last.size + leftover)
    else:
        end = 0
    found_words = tail_words[:end]
    sm = SequenceMatcher(None, exp_words, found_words, autojunk=False)

    diffs = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op != "equal":
            diffs.append((" ".join(exp_words[i1:i2]), " ".join(found_words[j1:j2])))

    body_matches = _span_matches(" ".join(found_words), text[header.end():])
    body_end = header.end() + body_matches[0].end() if body_matches else header.end()
    found_text = text[header.start():body_end].strip() if body_matches else (
        header_raw.strip() + " " + " ".join(found_words)).strip()
    char_ratio = SequenceMatcher(None, " ".join(exp_words), " ".join(found_words)).ratio()

    if problems:
        return _result(f, FAIL, STANDARD_WARNING, found_text, " ".join(problems))
    if not diffs:
        return _result(f, PASS, STANDARD_WARNING, found_text,
                       "Wording matches exactly. Bold type can't be confirmed from a photo; check visually.")

    def describe(want: str, got: str) -> str:
        if not want:
            return f"extra words \u201c{got}\u201d"
        if not got:
            return f"missing \u201c{want}\u201d"
        return f"\u201c{got}\u201d instead of \u201c{want}\u201d"

    diff_text = "; ".join(describe(w, g) for w, g in diffs[:4])
    # Small character-level differences are most likely OCR noise.
    only_small = all(
        w and g and SequenceMatcher(None, w, g).ratio() >= 0.75 for w, g in diffs
    ) and char_ratio >= 0.96
    if only_small:
        return _result(f, REVIEW, STANDARD_WARNING, found_text,
                       f"Nearly identical; possible scanning errors ({diff_text}). Please confirm the wording.")
    return _result(f, FAIL, STANDARD_WARNING, found_text, f"Wording differs from the required text: {diff_text}.")


# --------------------------------------------------------------------------
# Whole-label verification
# --------------------------------------------------------------------------

def verify_label(application: dict[str, str], label_text: str) -> dict:
    """Run every applicable check. Empty optional fields are skipped."""
    results: list[FieldResult] = []
    text_fields = [Field.BRAND, Field.CLASS_TYPE, Field.BOTTLER, Field.ORIGIN]
    for fld in [Field.BRAND, Field.CLASS_TYPE, Field.ALCOHOL, Field.NET, Field.BOTTLER, Field.ORIGIN]:
        value = (application.get(fld.value) or "").strip()
        if not value:
            continue
        if fld in text_fields:
            results.append(check_text_field(fld, value, label_text))
        elif fld is Field.ALCOHOL:
            results.append(check_alcohol(value, label_text))
        else:
            results.append(check_net_contents(value, label_text))

    if str(application.get("check_warning", "true")).lower() != "false":
        results.append(check_warning(label_text))

    statuses = {r.status for r in results}
    overall = FAIL if FAIL in statuses else REVIEW if REVIEW in statuses else PASS
    return {"overall": overall, "fields": [r.to_dict() for r in results]}

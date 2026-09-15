"use strict";

const FIELDS = ["brand_name", "class_type", "alcohol_content", "net_contents", "bottler", "country_of_origin"];
const STATUS = {
  pass: { word: "Matches", symbol: "✓", summary: "Everything checked matches the application." },
  review: { word: "Needs review", symbol: "!", summary: "Probably fine, but look at the highlighted items." },
  fail: { word: "Doesn't match", symbol: "✕", summary: "One or more items don't match the application." },
};
const BATCH_CONCURRENCY = 3;
const REQUEST_TIMEOUT_MS = 45000;

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ---------------------------------------------------------------- tabs */
const tabs = [$("#tab-one"), $("#tab-many")];
function selectTab(tab) {
  tabs.forEach((t) => {
    const on = t === tab;
    t.setAttribute("aria-selected", on);
    t.tabIndex = on ? 0 : -1;
    document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
  });
  tab.focus();
}
tabs.forEach((t, i) => {
  t.addEventListener("click", () => selectTab(t));
  t.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") selectTab(tabs[(i + 1) % tabs.length]);
  });
});

/* ------------------------------------------------------------- API call */
async function verify(file, application, checkWarning = true) {
  const body = new FormData();
  body.append("image", file, file.name || "label.png");
  FIELDS.forEach((f) => body.append(f, application[f] || ""));
  body.append("check_warning", checkWarning ? "true" : "false");

  for (let attempt = 0; attempt < 2; attempt++) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
    try {
      const res = await fetch("/api/verify", { method: "POST", body, signal: ctrl.signal });
      const data = await res.json().catch(() => ({}));
      if (res.ok) return data;
      if (res.status < 500 || attempt === 1) {
        throw new Error(typeof data.detail === "string" ? data.detail : `The server returned an error (${res.status}).`);
      }
    } catch (err) {
      if (err.name === "AbortError") err = new Error("The check took too long. Try again, or use a smaller photo.");
      else if (err instanceof TypeError) err = new Error("Couldn't reach the server. Check your connection and try again.");
      if (attempt === 1 || !/server|reach/i.test(err.message)) throw err;
    } finally {
      clearTimeout(timer);
    }
  }
}

/* ------------------------------------------------------- result render */
function renderFields(result) {
  if (!result.fields.length) return "";
  const rows = result.fields.map((f) => {
    const s = STATUS[f.status];
    return `<div class="frow">
      <span class="badge ${f.status}"><span aria-hidden="true">${s.symbol}</span> ${s.word}</span>
      <div>
        <h3>${esc(f.label)}</h3>
        <p class="note">${esc(f.note)}</p>
        <div class="compare">
          <div><span class="k">Application</span>${esc(f.expected)}</div>
          <div><span class="k">Read from label</span>${f.found ? esc(f.found) : "<em>Not found</em>"}</div>
        </div>
      </div>
    </div>`;
  });
  return `<div class="fields">${rows.join("")}</div>`;
}

function renderResult(result) {
  const s = STATUS[result.overall];
  const counts = result.fields.reduce((a, f) => ((a[f.status] = (a[f.status] || 0) + 1), a), {});
  const detail = result.fields.length
    ? `${result.fields.length} items checked: ${counts.pass || 0} match, ${counts.review || 0} need review, ${counts.fail || 0} don't match.`
    : s.summary;
  const notes = (result.notes || []).map((n) => `<li>${esc(n)}</li>`).join("");
  return `<div class="verdict ${result.overall}">
      <div class="mark" aria-hidden="true"><span>${s.symbol}</span></div>
      <div><h2>${s.word}</h2><p>${esc(detail)} Checked in ${result.seconds}s.</p></div>
    </div>
    ${notes ? `<ul class="notes">${notes}</ul>` : ""}
    ${renderFields(result)}
    ${result.ocr_text ? `<details class="raw"><summary>Show all text read from the label</summary><pre>${esc(result.ocr_text)}</pre></details>` : ""}`;
}

function showError(el, message) {
  el.textContent = message;
  el.hidden = !message;
}

/* --------------------------------------------------------- one label */
const drop = $("#drop");
const imageInput = $("#image");
const preview = $("#preview");
const resultBox = $("#single-result");
const singleError = $("#single-error");
let currentFile = null;
let previewUrl = null;

function setImage(file) {
  currentFile = file;
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = URL.createObjectURL(file);
  preview.src = previewUrl;
  preview.alt = `Label image: ${file.name}`;
  preview.hidden = false;
  $("#drop-text").hidden = true;
  drop.classList.add("has-image");
  resultBox.innerHTML = "";
  showError(singleError, "");
}

imageInput.addEventListener("change", () => imageInput.files[0] && setImage(imageInput.files[0]));
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("dragging"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, () => drop.classList.remove("dragging")));
drop.addEventListener("drop", (e) => {
  e.preventDefault();
  const file = e.dataTransfer.files[0];
  if (file) setImage(file);
});

let samples = [];
fetch("/api/samples").then((r) => r.json()).then((list) => {
  samples = list;
  const select = $("#sample");
  list.forEach((s, i) => select.insertAdjacentHTML("beforeend", `<option value="${i}">${esc(s.description)}</option>`));
  $("#batch-samples").hidden = list.length === 0;
}).catch(() => { $("#sample").closest(".sample-row").hidden = true; });

async function fetchSampleFile(sample) {
  const blob = await (await fetch(sample.url)).blob();
  return new File([blob], sample.filename, { type: blob.type });
}

$("#sample").addEventListener("change", async (e) => {
  const sample = samples[e.target.value];
  if (!sample) return;
  try {
    setImage(await fetchSampleFile(sample));
    FIELDS.forEach((f) => { document.getElementById(f).value = sample[f] || ""; });
    if (sample.bottler || sample.country_of_origin) $(".more").open = true;
  } catch {
    showError(singleError, "Couldn't load that sample. Try another.");
  }
});

$("#single-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  showError(singleError, "");
  const application = Object.fromEntries(FIELDS.map((f) => [f, document.getElementById(f).value.trim()]));
  const checkWarning = $("#check_warning").checked;

  if (!currentFile) return showError(singleError, "Choose a label image first.");
  if (!checkWarning && !FIELDS.some((f) => application[f])) {
    return showError(singleError, "Enter at least one value from the application, or turn on the warning check.");
  }

  const btn = $("#check-btn");
  btn.disabled = true;
  btn.textContent = "Checking…";
  resultBox.innerHTML = `<div class="loading"><div class="spinner" aria-hidden="true"></div>Reading the label…</div>`;
  try {
    const result = await verify(currentFile, application, checkWarning);
    resultBox.innerHTML = renderResult(result);
    resultBox.focus();
    resultBox.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  } catch (err) {
    resultBox.innerHTML = "";
    showError(singleError, err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Check label";
  }
});

/* -------------------------------------------------------- many labels */
let batchImages = [];
let batchRows = new Map(); // lowercased filename -> application row
const batchError = $("#batch-error");

function parseCsv(text) {
  const rows = [];
  let row = [], cell = "", quoted = false;
  text = text.replace(/^\uFEFF/, "");
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (c === '"') quoted = false;
      else cell += c;
    } else if (c === '"') quoted = true;
    else if (c === ",") { row.push(cell); cell = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(cell); rows.push(row); row = []; cell = "";
    } else cell += c;
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  return rows.filter((r) => r.some((c) => c.trim()));
}

const HEADER_ALIASES = {
  file: "filename", file_name: "filename", image: "filename",
  brand: "brand_name", class: "class_type", type: "class_type", class_type_designation: "class_type",
  abv: "alcohol_content", alcohol: "alcohol_content", net: "net_contents", volume: "net_contents",
  bottler_name_and_address: "bottler", name_and_address: "bottler", country: "country_of_origin", origin: "country_of_origin",
};

function loadManifest(text) {
  const rows = parseCsv(text);
  if (rows.length < 2) throw new Error("The spreadsheet has no data rows.");
  const headers = rows[0].map((h) => {
    const key = h.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
    return HEADER_ALIASES[key] || key;
  });
  if (!headers.includes("filename")) throw new Error("The spreadsheet needs a \"filename\" column.");
  const map = new Map();
  rows.slice(1).forEach((r) => {
    const rec = Object.fromEntries(headers.map((h, i) => [h, (r[i] || "").trim()]));
    if (rec.filename) map.set(rec.filename.split(/[\\/]/).pop().toLowerCase(), rec);
  });
  return map;
}

function updateBatchInfo() {
  const n = batchImages.length;
  $("#batch-images-info").textContent = n ? `${n} image${n === 1 ? "" : "s"} chosen.` : "No images chosen.";
  if (batchRows.size) {
    const matched = batchImages.filter((f) => batchRows.has(f.name.toLowerCase())).length;
    $("#batch-csv-info").textContent = `${batchRows.size} rows loaded` + (n ? `; ${matched} of ${n} images have a matching row.` : ".");
  }
  $("#batch-run").disabled = n === 0;
}

$("#batch-images").addEventListener("change", (e) => {
  batchImages = [...e.target.files].filter((f) => f.type.startsWith("image/"));
  showError(batchError, batchImages.length > 500 ? "Choose 500 images or fewer at a time." : "");
  if (batchImages.length > 500) batchImages = [];
  updateBatchInfo();
});

$("#batch-csv").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    batchRows = loadManifest(await file.text());
    showError(batchError, "");
  } catch (err) {
    batchRows = new Map();
    showError(batchError, err.message);
  }
  updateBatchInfo();
});

function download(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const csvCell = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;

$("#template-link").addEventListener("click", (e) => {
  e.preventDefault();
  download("label-check-template.csv",
    ["filename", ...FIELDS].join(",") + "\r\n" +
    ["example_label.jpg", "OLD TOM DISTILLERY", "Kentucky Straight Bourbon Whiskey", "45% Alc./Vol. (90 Proof)", "750 mL", "", ""].map(csvCell).join(",") + "\r\n");
});

$("#batch-samples").addEventListener("click", async () => {
  try {
    batchImages = await Promise.all(samples.map(fetchSampleFile));
    batchRows = new Map(samples.map((s) => [s.filename.toLowerCase(), s]));
    showError(batchError, "");
    updateBatchInfo();
  } catch {
    showError(batchError, "Couldn't load the sample labels.");
  }
});

let batchResults = [];
let batchFilter = "all";

$("#batch-run").addEventListener("click", async () => {
  const runBtn = $("#batch-run");
  runBtn.disabled = true;
  showError(batchError, "");
  batchResults = new Array(batchImages.length);
  batchFilter = "all";
  $("#batch-summary").hidden = true;
  $("#batch-results").innerHTML = "";
  $("#batch-progress").hidden = false;

  const total = batchImages.length;
  let done = 0, next = 0;
  const started = performance.now();
  const tick = () => {
    $("#bar-fill").style.width = `${(done / total) * 100}%`;
    $("#progress-text").textContent = done < total ? `Checked ${done} of ${total} labels…` : `Checked all ${total} labels.`;
  };
  tick();

  async function worker() {
    while (next < total) {
      const i = next++;
      const file = batchImages[i];
      const row = batchRows.get(file.name.toLowerCase());
      try {
        const res = await verify(file, row || {}, true);
        if (!row) res.notes = ["No row for this image in the spreadsheet, so only the government warning was checked.", ...(res.notes || [])];
        batchResults[i] = { file: file.name, ...res };
      } catch (err) {
        batchResults[i] = { file: file.name, overall: "fail", fields: [], notes: [`Couldn't check: ${err.message}`], seconds: 0, error: true };
      }
      done++;
      tick();
    }
  }
  await Promise.all(Array.from({ length: Math.min(BATCH_CONCURRENCY, total) }, worker));

  const secs = ((performance.now() - started) / 1000).toFixed(0);
  $("#progress-text").textContent = `Checked all ${total} labels in ${secs}s.`;
  renderBatch();
  runBtn.disabled = false;
});

function renderBatch() {
  const counts = { pass: 0, review: 0, fail: 0 };
  batchResults.forEach((r) => counts[r.overall]++);
  const filterBtn = (key, label) =>
    `<button class="badge filter-btn ${key === "all" ? "" : key}" data-filter="${key}" aria-pressed="${batchFilter === key}">${label}</button>`;
  const summary = $("#batch-summary");
  summary.hidden = false;
  summary.innerHTML = `
    ${filterBtn("all", `All ${batchResults.length}`)}
    ${filterBtn("fail", `✕ ${counts.fail} don't match`)}
    ${filterBtn("review", `! ${counts.review} need review`)}
    ${filterBtn("pass", `✓ ${counts.pass} match`)}
    <span class="spacer"></span>
    <button class="quiet" id="batch-download">Download results (CSV)</button>`;
  summary.querySelectorAll(".filter-btn").forEach((b) => b.addEventListener("click", () => { batchFilter = b.dataset.filter; renderBatch(); }));
  $("#batch-download").addEventListener("click", downloadResults);

  // Problems first, so agents start with what needs attention.
  const order = { fail: 0, review: 1, pass: 2 };
  const shown = batchResults
    .map((r, i) => ({ r, i }))
    .filter(({ r }) => batchFilter === "all" || r.overall === batchFilter)
    .sort((a, b) => order[a.r.overall] - order[b.r.overall] || a.r.file.localeCompare(b.r.file));

  const rows = shown.map(({ r, i }) => {
    const s = STATUS[r.overall];
    const issues = r.fields.filter((f) => f.status !== "pass").map((f) => f.label);
    const issueText = r.error ? r.notes[0] : issues.length ? issues.join(", ") : "None";
    return `<tr class="item" data-i="${i}" tabindex="0" aria-expanded="false">
        <td><span class="badge ${r.overall}"><span aria-hidden="true">${s.symbol}</span> ${s.word}</span></td>
        <td>${esc(r.file)}</td>
        <td class="issues">${esc(issueText)}</td>
      </tr>`;
  });
  $("#batch-results").innerHTML = shown.length
    ? `<div class="table-scroll"><table class="btable">
        <thead><tr><th>Result</th><th>File</th><th>Items to look at</th></tr></thead>
        <tbody>${rows.join("")}</tbody></table></div>`
    : `<p class="hint">No labels in this group.</p>`;

  $("#batch-results").querySelectorAll("tr.item").forEach((tr) => {
    const toggle = () => {
      const open = tr.getAttribute("aria-expanded") === "true";
      if (open) tr.nextElementSibling.remove();
      else {
        const r = batchResults[tr.dataset.i];
        const notes = (r.notes || []).map((n) => `<li>${esc(n)}</li>`).join("");
        tr.insertAdjacentHTML("afterend",
          `<tr class="detail"><td colspan="3">${notes ? `<ul class="notes">${notes}</ul>` : ""}${renderFields(r)}</td></tr>`);
      }
      tr.setAttribute("aria-expanded", String(!open));
    };
    tr.addEventListener("click", toggle);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });
  });
}

function downloadResults() {
  const header = ["filename", "overall_result", "field", "field_result", "application_value", "label_value", "note"];
  const lines = [header.join(",")];
  batchResults.forEach((r) => {
    if (!r.fields.length) {
      lines.push([r.file, r.overall, "", "", "", "", (r.notes || []).join(" ")].map(csvCell).join(","));
    }
    r.fields.forEach((f) => {
      lines.push([r.file, r.overall, f.label, f.status, f.expected, f.found, f.note].map(csvCell).join(","));
    });
  });
  download(`label-check-results-${new Date().toISOString().slice(0, 10)}.csv`, lines.join("\r\n") + "\r\n");
}

/* PU coatings database: page logic.
   Talks to the API in app.py (/api/summary, /api/properties, /api/records, /api/papers).
   All text from the database goes into the page with textContent / DOM nodes, never innerHTML. */

"use strict";

const $ = (id) => document.getElementById(id);

const reducedMotion =
  typeof window.matchMedia === "function" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const state = {
  properties: [],
  rows: new Map(), // record id -> { row, detail, toggle }
};

/* ---------- small helpers ---------- */

/** Build an element. attrs: {class, text, ...attributes}. Children can be nodes or strings. */
function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "text") node.textContent = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children) {
    if (child !== null && child !== undefined) node.append(child);
  }
  return node;
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

async function getJson(url) {
  let response;
  try {
    response = await fetch(url);
  } catch (error) {
    throw new Error("Cannot reach the database service. Start it with: python app.py");
  }
  if (!response.ok) {
    let detail = `The request failed (status ${response.status}).`;
    try {
      const body = await response.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch (ignored) {
      /* keep the default message */
    }
    throw new Error(detail);
  }
  return response.json();
}

function paperUrl(pmcid) {
  return `https://pmc.ncbi.nlm.nih.gov/articles/${encodeURIComponent(pmcid)}/`;
}

function paperName(record) {
  return record.paper_title && record.paper_title.trim() ? record.paper_title : record.pmcid;
}

function formatValue(record) {
  const value = record.value_text === null || record.value_text === undefined ? "" : record.value_text;
  if (!record.unit) return value;
  return record.unit === "°" || record.unit === "%" ? `${value}${record.unit}` : `${value} ${record.unit}`;
}

function showMessage(text, isError) {
  const box = $("message");
  box.textContent = text;
  box.classList.toggle("is-error", Boolean(isError));
  box.hidden = false;
}

function hideMessage() {
  $("message").hidden = true;
}

/* ---------- top of the page ---------- */

function renderSummary(summary) {
  if (!summary.records) {
    $("lede").textContent =
      "No values are stored yet. Run load_to_db.py, restart app.py, then reload this page.";
    return;
  }
  const parts = [
    `The database holds ${plural(summary.records, "value")} from ${plural(summary.papers_with_values, "paper")} on waterborne polyurethane coatings.`,
    `${summary.verified_records} of them are verified against the paper text.`,
    "Pick a property to see each value next to the sentence it came from.",
  ];
  $("lede").textContent = parts.join(" ");

  const models = summary.models && summary.models.length ? summary.models.join(", ") : null;
  $("footer").textContent =
    (models ? `Values were extracted with ${models}. ` : "") +
    "Papers come from Europe PMC. Open any paper link to check a value yourself.";
}

function renderPropertyOptions(properties) {
  const select = $("property");
  select.replaceChildren();
  for (const item of properties) {
    const label = `${item.property} (${item.verified} verified of ${item.total})`;
    select.append(el("option", { value: item.property, text: label }));
  }
  select.disabled = properties.length === 0;
}

/* ---------- dot plot ---------- */

function niceStep(rough) {
  const power = Math.pow(10, Math.floor(Math.log10(rough)));
  const fraction = rough / power;
  const nice = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10;
  return nice * power;
}

function buildTicks(lo, hi) {
  const step = niceStep((hi - lo) / 6);
  const decimals = Math.min(6, Math.max(0, -Math.floor(Math.log10(step))));
  const ticks = [];
  for (let i = Math.ceil(lo / step - 1e-9); i * step <= hi + 1e-9; i++) {
    ticks.push(Number((i * step).toFixed(decimals)));
    if (ticks.length > 20) break;
  }
  return ticks;
}

function renderStrip(records, propertyName) {
  const host = $("strip");
  const note = $("strip-note");
  host.replaceChildren();
  note.hidden = true;

  const numeric = records.filter((r) => typeof r.value_num === "number" && Number.isFinite(r.value_num));

  // Plot one unit only, so values on the same scale are comparable.
  const unitCounts = new Map();
  for (const r of numeric) {
    const key = r.unit || "";
    unitCounts.set(key, (unitCounts.get(key) || 0) + 1);
  }
  let unit = "";
  let best = -1;
  for (const [key, count] of unitCounts) {
    if (count > best) {
      best = count;
      unit = key;
    }
  }
  const points = numeric.filter((r) => (r.unit || "") === unit);

  if (points.length === 0) {
    $("plot-panel").hidden = true;
    return;
  }
  $("plot-panel").hidden = false;

  const skipped = records.length - points.length;
  if (skipped > 0) {
    note.textContent =
      `${plural(skipped, "value")} not plotted because it is not a plain number or uses a different unit. ` +
      "Every value is still in the table below.";
    note.hidden = false;
  }

  // Scale
  const values = points.map((r) => r.value_num);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min;
  const pad = span > 0 ? span * 0.06 : Math.max(1, Math.abs(max) * 0.05);
  let lo = min - pad;
  const hi = max + pad;
  if (min >= 0 && lo < 0) lo = 0;
  const pct = (v) => ((v - lo) / (hi - lo)) * 100;
  const ticks = buildTicks(lo, hi);

  // Group dots by paper, keeping the order the API gave
  const groups = new Map();
  for (const r of points) {
    if (!groups.has(r.pmcid)) groups.set(r.pmcid, []);
    groups.get(r.pmcid).push(r);
  }

  const wrap = el("div", {
    class: "strip",
    role: "group",
    "aria-label": `Values of ${propertyName}, one row per paper. The table below lists the same values.`,
  });

  const grid = el("div", { class: "strip-grid", "aria-hidden": "true" });
  for (const t of ticks) {
    grid.append(el("span", { class: "strip-gridline", style: `left:${pct(t).toFixed(3)}%` }));
  }
  wrap.append(grid);

  const offsets = [0, -10, 10, -20, 20]; // vertical nudges for dots that would overlap
  for (const [pmcid, items] of groups) {
    const first = items[0];
    const label = el(
      "div",
      { class: "strip-label" },
      el("span", { class: "strip-title", text: paperName(first) }),
      first.paper_title && first.paper_title.trim()
        ? el("span", { class: "strip-id", text: pmcid })
        : null
    );
    const track = el("div", { class: "strip-track" });

    const sorted = [...items].sort((a, b) => a.value_num - b.value_num);
    const lastAtLevel = offsets.map(() => -Infinity);
    for (const r of sorted) {
      const x = pct(r.value_num);
      let level = lastAtLevel.findIndex((last) => x - last >= 3.2);
      if (level === -1) level = 0;
      lastAtLevel[level] = x;

      const description =
        `${formatValue(r)}: ${r.sample || "sample not named"}` +
        (r.condition ? `, ${r.condition}` : "") +
        (r.verified ? " (verified)" : " (not verified)");
      const dot = el("button", {
        class: r.verified ? "dot" : "dot is-unverified",
        type: "button",
        title: description,
        "aria-label": description,
        style: `left:${x.toFixed(3)}%; top:calc(50% + ${offsets[level]}px)`,
      });
      dot.addEventListener("click", () => focusRecord(r.id));
      track.append(dot);
    }
    wrap.append(el("div", { class: "strip-row" }, label, track));
  }

  const axis = el("div", { class: "strip-axis", "aria-hidden": "true" });
  for (const t of ticks) {
    axis.append(el("span", { class: "strip-tick", style: `left:${pct(t).toFixed(3)}%`, text: String(t) }));
  }
  axis.append(el("span", { class: "strip-unit", text: unit ? `Unit: ${unit}` : "No unit given" }));
  wrap.append(axis);

  host.append(wrap);
}

/* ---------- table ---------- */

function evidenceNode(record) {
  const text = record.evidence || "";
  const needle = record.value_text || "";
  const at = needle ? text.indexOf(needle) : -1;
  const quote = el("blockquote", { class: "evidence-text" });
  if (!text) {
    quote.append("No evidence sentence was stored for this value.");
  } else if (at === -1) {
    quote.append(text);
  } else {
    quote.append(text.slice(0, at), el("mark", { text: needle }), text.slice(at + needle.length));
  }
  return quote;
}

function setExpanded(id, open) {
  const entry = state.rows.get(id);
  if (!entry) return;
  entry.detail.hidden = !open;
  entry.toggle.setAttribute("aria-expanded", open ? "true" : "false");
  entry.toggle.textContent = open ? "Hide evidence" : "Show evidence";
}

function focusRecord(id) {
  const entry = state.rows.get(id);
  if (!entry) return;
  setExpanded(id, true);
  entry.row.scrollIntoView({ block: "center", behavior: reducedMotion ? "auto" : "smooth" });
  entry.row.classList.add("is-focus");
  setTimeout(() => entry.row.classList.remove("is-focus"), 2500);
}

function renderTable(records) {
  const body = $("results-body");
  body.replaceChildren();
  state.rows.clear();

  for (const r of records) {
    const detailId = `evidence-${r.id}`;
    const toggle = el("button", {
      class: "link-button",
      type: "button",
      "aria-expanded": "false",
      "aria-controls": detailId,
      text: "Show evidence",
    });

    const paperCell = el(
      "td",
      { class: "cell-paper" },
      el("a", {
        class: "paper-id",
        href: paperUrl(r.pmcid),
        target: "_blank",
        rel: "noopener",
        title: r.paper_title && r.paper_title.trim() ? r.paper_title : null,
        text: r.pmcid,
      })
    );

    const row = el(
      "tr",
      { class: "data-row", id: `row-${r.id}` },
      el("td", { class: "cell-sample", text: r.sample || "Not named" }),
      el("td", { class: "cell-value", text: formatValue(r) }),
      el("td", { class: "cell-condition", text: r.condition || "None stated" }),
      paperCell,
      el("td", {}, el("span", { class: r.verified ? "status" : "status is-unverified", text: r.verified ? "Verified" : "Not verified" })),
      el("td", {}, toggle)
    );

    const detail = el(
      "tr",
      { class: "evidence-row", id: detailId, hidden: true },
      el(
        "td",
        { colspan: "6" },
        evidenceNode(r),
        el("p", { class: "evidence-meta", text: `Section: ${r.section_title || "not recorded"}` })
      )
    );

    toggle.addEventListener("click", () => setExpanded(r.id, detail.hidden));
    state.rows.set(r.id, { row, detail, toggle });
    body.append(row, detail);
  }
}

/* ---------- loading a property ---------- */

async function loadRecords() {
  const property = $("property").value;
  if (!property) return;
  const verifiedOnly = $("verified-only").checked;

  let records;
  try {
    const query = new URLSearchParams({ property, verified_only: String(verifiedOnly) });
    records = await getJson(`/api/records?${query.toString()}`);
  } catch (error) {
    $("results").hidden = true;
    showMessage(error.message, true);
    return;
  }

  hideMessage();

  if (records.length === 0) {
    $("results").hidden = true;
    const info = state.properties.find((p) => p.property === property);
    if (verifiedOnly && info && info.total > info.verified) {
      showMessage(
        `No verified values for ${property}, but ${info.total - info.verified} unverified ` +
          "value(s) exist. Untick “Verified values only” to see them."
      );
    } else {
      showMessage(`No values for ${property} were found in the papers read so far.`);
    }
    return;
  }

  $("results").hidden = false;
  const paperCount = new Set(records.map((r) => r.pmcid)).size;
  $("result-count").textContent = `Showing ${plural(records.length, "value")} from ${plural(paperCount, "paper")}.`;
  renderStrip(records, property);
  renderTable(records);
}

/* ---------- papers list ---------- */

function renderPapers(papers) {
  const list = $("papers-list");
  list.replaceChildren();
  for (const p of papers) {
    const hasTitle = Boolean(p.title && p.title.trim());
    let detail;
    if (!p.relevant) detail = "Not about waterborne polyurethane coatings, so no values were kept.";
    else if (p.records === 0) detail = "Relevant, but none of the five properties were reported in the text.";
    else detail = `${plural(p.records, "value")}, ${p.verified} verified.`;

    list.append(
      el(
        "li",
        {},
        el("a", { class: "paper-title", href: paperUrl(p.pmcid), target: "_blank", rel: "noopener", text: hasTitle ? p.title : p.pmcid }),
        el("span", { class: "paper-detail", text: hasTitle ? `${p.pmcid}. ${detail}` : detail })
      )
    );
  }
}

/* ---------- start ---------- */

async function init() {
  let summary, properties, papers;
  try {
    [summary, properties, papers] = await Promise.all([
      getJson("/api/summary"),
      getJson("/api/properties"),
      getJson("/api/papers"),
    ]);
  } catch (error) {
    $("lede").textContent = "The database could not be loaded.";
    showMessage(error.message, true);
    return;
  }

  state.properties = properties;
  renderSummary(summary);
  renderPapers(papers);

  if (properties.length === 0) {
    showMessage("The database has no values yet. Run load_to_db.py, restart app.py, then reload this page.");
    return;
  }

  renderPropertyOptions(properties);
  $("property").addEventListener("change", loadRecords);
  $("verified-only").addEventListener("change", loadRecords);
  await loadRecords();
}

init();

"""
F_parsePapers.py

XML -> structured JSON in ONE file: normal text AND tables.
(This replaces both F_xmlToJSON.py and the old F_parsePapers.py. It does not import
anything from F_xmlToJSON.py any more, so that file is no longer needed.)

Reads every raw_xml/<PMCID>.xml and writes structured_json/<PMCID>.json.

Output shape per paper:
    {
      "pmcid": "PMC123456",
      "title": "...",
      "abstract": ["para 1", "para 2"],
      "body": [
        {"title": "1. Introduction",
         "paragraphs": ["...", "[Table 1 is in the tables list]", "..."],
         "subsections": [ ...same shape... ]}
      ],
      "tables": [
        {"id": "T1", "label": "Table 1", "caption": "...",
         "headers": [["Sample", "Tensile", "Tensile"], ["", "Strength (MPa)", "Elongation (%)"]],
         "rows": [["WPU", "4.1", "320"]],
         "footnotes": ["..."]}
      ]
    }

Tables live in two places in JATS XML: inline inside a section or paragraph, and in a
<floats-group> at the end of the file. Both are collected into "tables". Where a table sat
in the text, a short marker such as "[Table 1 is in the tables list]" is left behind.

Usage:
    python F_parsePapers.py                 # every file in raw_xml/
    python F_parsePapers.py PMC12217702     # only the PMC IDs you list
"""

import json
import re
import sys
from pathlib import Path

from lxml import etree

RAW_XML_DIR = Path("raw_xml")
OUTPUT_DIR = Path("structured_json")

TABLE_MARKER = "[{label} is in the tables list]"

# In-text references that should stay in the sentence ("see Table 2", "Figure 3a").
# Citation markers like [2] (ref-type "bibr") are dropped, as before.
KEEP_XREF_TYPES = {"table", "fig", "supplementary-material", "sec", "disp-formula", "app"}


# ---------------------------------------------------------------------------
# Basic XML helpers
# ---------------------------------------------------------------------------

def local_tag(element):
    """Tag name without any XML namespace. Only call on real elements."""
    return etree.QName(element).localname


def is_real_element(node):
    """False for comment and processing-instruction nodes (their .tag is not a string)."""
    return isinstance(node.tag, str)


def find_by_local_name(root, name):
    """Find elements by tag name whether or not the file uses an XML namespace."""
    return root.xpath(f".//*[local-name()='{name}']")


def table_label(wrap):
    """The label of a table-wrap ('Table 3'), or 'Table' if it has none."""
    labels = wrap.xpath(".//*[local-name()='label']")
    if labels:
        text = parse_paragraph(labels[0])
        if text:
            return text
    return wrap.get("id") or "Table"


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def stitch_inline_text(element):
    """
    Rebuild the text of an element, keeping the text inside inline tags such as <sub> and
    <sup> (so SiO<sub>2</sub> stays "SiO2"). Special cases:
      - a <table-wrap> sitting inside a paragraph becomes a short marker
      - citation markers like [2] are dropped, but "Table 2" / "Figure 3" references stay
      - the text that follows any dropped element (its .tail) is always kept
    """
    parts = []
    if element.text:
        parts.append(element.text)

    for child in element:
        if is_real_element(child):
            tag = local_tag(child)
            if tag in ("table-wrap", "table-wrap-group"):
                parts.append(" " + TABLE_MARKER.format(label=table_label(child)) + " ")
            elif tag == "xref" and child.get("ref-type") not in KEEP_XREF_TYPES:
                pass  # citation marker: drop its own text, keep the tail below
            else:
                parts.append(stitch_inline_text(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def parse_paragraph(p_element):
    """Full clean text of a <p> (or title, or table cell) element."""
    text = stitch_inline_text(p_element)
    text = " ".join(text.split())
    # Removing a citation can leave a stray space before punctuation: "cited , and" -> "cited, and"
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    return text


def parse_title(root):
    """The paper's title, taken from the article metadata, keeping text inside <sub>/<sup>."""
    candidates = root.xpath(
        ".//*[local-name()='article-meta']//*[local-name()='article-title']"
    ) or find_by_local_name(root, "article-title")
    return parse_paragraph(candidates[0]) if candidates else ""


def parse_abstract(root):
    """
    Abstract paragraphs. Looks at every <p> inside the abstract, so structured abstracts
    (Background / Methods / Results sub-sections) are included too.
    """
    paragraphs = []
    for abstract_el in find_by_local_name(root, "abstract"):
        for p in abstract_el.xpath(".//*[local-name()='p']"):
            text = parse_paragraph(p)
            if text:
                paragraphs.append(text)
    return paragraphs


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

def parse_row(tr):
    """One table row as a list of cell strings. A cell with colspan=3 is repeated 3 times so
    header cells stay lined up with the columns below them."""
    cells = []
    for child in tr:
        if not is_real_element(child) or local_tag(child) not in ("th", "td"):
            continue
        text = parse_paragraph(child)
        try:
            span = max(1, min(int(child.get("colspan") or 1), 20))
        except ValueError:
            span = 1
        cells.extend([text] * span)
    return cells


def parse_html_table(table_el):
    """Read the header rows and body rows of a <table>. rowspan is not expanded."""
    headers, rows = [], []

    theads = [c for c in table_el if is_real_element(c) and local_tag(c) == "thead"]
    tbodies = [c for c in table_el if is_real_element(c) and local_tag(c) == "tbody"]
    direct_trs = [c for c in table_el if is_real_element(c) and local_tag(c) == "tr"]

    for thead in theads:
        for tr in thead:
            if is_real_element(tr) and local_tag(tr) == "tr":
                cells = parse_row(tr)
                if cells:
                    headers.append(cells)

    if tbodies:
        for tbody in tbodies:
            for tr in tbody:
                if is_real_element(tr) and local_tag(tr) == "tr":
                    cells = parse_row(tr)
                    if cells:
                        rows.append(cells)
    elif direct_trs:
        # Some tables skip thead/tbody and put <tr> directly under <table>.
        if not headers:
            first = parse_row(direct_trs[0])
            if first:
                headers.append(first)
            direct_trs = direct_trs[1:]
        for tr in direct_trs:
            cells = parse_row(tr)
            if cells:
                rows.append(cells)

    return headers, rows


def parse_table_wrap(wrap):
    """Turn one <table-wrap> into a plain dict."""
    label = ""
    caption_parts = []
    footnotes = []
    headers, rows = [], []

    for child in wrap:
        if not is_real_element(child):
            continue
        tag = local_tag(child)

        if tag == "label":
            label = parse_paragraph(child)

        elif tag == "caption":
            for cap_child in child:
                if is_real_element(cap_child) and local_tag(cap_child) in ("title", "p"):
                    text = parse_paragraph(cap_child)
                    if text:
                        caption_parts.append(text)

        elif tag == "table":
            headers, rows = parse_html_table(child)

        elif tag == "table-wrap-foot":
            # <fn> usually contains a <p>; read the <fn> elements so text is not repeated.
            notes = find_by_local_name(child, "fn") or find_by_local_name(child, "p")
            for note in notes:
                text = parse_paragraph(note)
                if text:
                    footnotes.append(text)

    if not headers and not rows:
        # The <table> may be wrapped in <alternatives> or similar. Look deeper once.
        inner = find_by_local_name(wrap, "table")
        if inner:
            headers, rows = parse_html_table(inner[0])

    return {
        "id": wrap.get("id") or "",
        "label": label or wrap.get("id") or "Table",
        "caption": " ".join(caption_parts),
        "headers": headers,
        "rows": rows,
        "footnotes": footnotes,
    }


def collect_tables(root):
    """Every <table-wrap> in the paper in document order, including <floats-group>."""
    tables, seen = [], set()
    for wrap in find_by_local_name(root, "table-wrap"):
        parsed = parse_table_wrap(wrap)
        key = (parsed["id"], parsed["label"], parsed["caption"])
        if key in seen:
            continue
        seen.add(key)
        if parsed["headers"] or parsed["rows"] or parsed["caption"]:
            tables.append(parsed)
    return tables


# ---------------------------------------------------------------------------
# Sections and the whole paper
# ---------------------------------------------------------------------------

def parse_section(sec_element):
    """
    Recursively parse a <sec> into {"title", "paragraphs", "subsections"}.
    Only direct children are read, so the section hierarchy is kept. A table that sits
    directly in the section leaves a marker in the paragraph flow.
    """
    title_text = ""
    paragraphs = []
    subsections = []

    for child in sec_element:
        if not is_real_element(child):
            continue
        tag = local_tag(child)

        if tag == "title":
            title_text = parse_paragraph(child)
        elif tag == "p":
            text = parse_paragraph(child)
            if text:
                paragraphs.append(text)
        elif tag in ("table-wrap", "table-wrap-group"):
            paragraphs.append(TABLE_MARKER.format(label=table_label(child)))
        elif tag == "sec":
            subsections.append(parse_section(child))
        # Anything else (figures, supplementary files) is ignored on purpose.

    return {"title": title_text, "paragraphs": paragraphs, "subsections": subsections}


def parse_body(root):
    """The list of top-level body sections. Paragraphs that sit directly under <body>,
    outside any <sec>, are kept as a first section with an empty title."""
    body_matches = find_by_local_name(root, "body")
    if not body_matches:
        return []
    top = parse_section(body_matches[0])
    sections = top["subsections"]
    if top["paragraphs"]:
        sections.insert(0, {"title": "", "paragraphs": top["paragraphs"], "subsections": []})
    return sections


def parse_paper(root, pmcid):
    return {
        "pmcid": pmcid,
        "title": parse_title(root),
        "abstract": parse_abstract(root),
        "body": parse_body(root),
        "tables": collect_tables(root),
    }


def process_files(pmcids=None):
    OUTPUT_DIR.mkdir(exist_ok=True)

    if pmcids:
        xml_files = [RAW_XML_DIR / f"{p}.xml" for p in pmcids]
    else:
        xml_files = sorted(RAW_XML_DIR.glob("*.xml"))

    if not xml_files:
        print(f"No XML files found in {RAW_XML_DIR}/")
        return

    for xml_path in xml_files:
        pmcid = xml_path.stem
        if not xml_path.exists():
            print(f"[MISSING] {xml_path}")
            continue
        try:
            root = etree.parse(str(xml_path)).getroot()
        except etree.XMLSyntaxError as e:
            print(f"[SKIP] {pmcid}: could not parse XML ({e})")
            continue

        paper = parse_paper(root, pmcid)
        out_path = OUTPUT_DIR / f"{pmcid}.json"
        out_path.write_text(json.dumps(paper, indent=2, ensure_ascii=False), encoding="utf-8")

        n_wraps = len(find_by_local_name(root, "table-wrap"))
        n_tables = len(paper["tables"])
        n_rows = sum(len(t["rows"]) for t in paper["tables"])
        note = "" if n_wraps == n_tables else f"  [check: {n_wraps} table-wrap tags in the XML]"
        print(f"[OK] {pmcid} -> {out_path} ({n_tables} tables, {n_rows} body rows){note}")


if __name__ == "__main__":
    process_files(sys.argv[1:] or None)
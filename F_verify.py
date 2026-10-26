"""
F_verify.py

Evidence checks for extracted records. Needs no API key and no internet.

Every record gets two flags:
  evidence_found     - the evidence really exists in the paper
  value_in_evidence  - the value really appears in that evidence
A record counts as "verified" (on the website) only when both are true.

Text values: the evidence sentence must be in the paper's text, and must contain the value.
Table values (source == "table"): the evidence must be exactly one row of the named table,
the value must sit in that row, and if a column is named, the value must sit under that column.
When a table check fails, the record also gets "check_note" saying why.
"""

import re


def norm(text):
    """Collapse all whitespace so comparisons ignore spacing and line breaks."""
    return " ".join(str(text).split())


def paper_full_text(paper):
    """All running text of a structured paper (not the tables) as one normalized string."""
    parts = [paper.get("title", "")] + list(paper.get("abstract", []))

    def walk(sec):
        parts.append(sec.get("title", ""))
        parts.extend(sec.get("paragraphs", []))
        for sub in sec.get("subsections", []):
            walk(sub)

    for sec in paper.get("body", []):
        walk(sec)
    return norm(" ".join(parts))


def row_text(cells):
    """A table row as one string: cells joined with ' | ' (the format the prompt asks for)."""
    return norm(" | ".join(str(c) for c in cells)).strip(" |")


def value_matches_cell(value, cell):
    """
    True if the cell holds the value. '96.2' matches '96.2%' and '8.8 ± 0.3',
    but '4' does not match '4.1' and '12' does not match '125'.
    """
    v, c = norm(value), norm(cell)
    if not v or not c:
        return False
    if c == v:
        return True
    if c.startswith(v):
        return not re.match(r"\.?\d", c[len(v):])
    return False


def column_names(headers, index):
    """All reasonable spellings of the header of one column. Two header rows are joined
    with ' / ', top row first, so 'Aging Process / UV 24 h % Reduction'."""
    parts = [norm(row[index]) for row in headers if index < len(row)]
    names = set()
    non_empty = [p for p in parts if p]
    if non_empty:
        names.add(" / ".join(non_empty))
        names.update(non_empty)
    if parts:
        names.add(" / ".join(parts))
    return names


def check_table_record(rec, tables):
    """Returns (evidence_found, value_in_evidence, note). note is '' when everything passes."""
    label = norm(rec.get("table") or "").lower()
    matching = [t for t in tables if norm(t.get("label", "")).lower() == label]
    if not matching:
        return False, False, "that table is not in the paper's tables list"

    evidence = norm(rec.get("evidence") or "").strip(" |")
    value = rec.get("value")
    column = norm(rec.get("column") or "")

    for table in matching:
        headers = table.get("headers") or []
        width = max((len(h) for h in headers), default=0)
        for row in table.get("rows") or []:
            if row_text(row) != evidence:
                continue

            # The evidence is a real row of this table. Now check the value.
            if not any(value_matches_cell(value, cell) for cell in row):
                return True, False, "the value is not in that row"
            if not column:
                return True, True, ""
            if width == 0:
                return True, True, "column not checked: the table has no headers"

            named = [i for i in range(width) if column in column_names(headers, i)]
            if not named:
                return True, False, "the column name is not a header of that table"

            # A merged cell can leave a row shorter than the header; line up from the right.
            offset = max(width - len(row), 0)
            for i in named:
                j = i - offset
                if 0 <= j < len(row) and value_matches_cell(value, row[j]):
                    return True, True, ""
            return True, False, "the value is in that row but not under the named column"

    return False, False, "the evidence is not a row of that table"


def check_records(result, paper):
    """Add evidence_found and value_in_evidence (and check_note for tables) to every record."""
    full_text = paper_full_text(paper)
    tables = paper.get("tables") or []

    for rec in result["records"]:
        if not isinstance(rec, dict):
            continue
        rec.pop("check_note", None)

        if rec.get("source") == "table":
            found, ok, note = check_table_record(rec, tables)
            rec["evidence_found"] = found
            rec["value_in_evidence"] = ok
            if note:
                rec["check_note"] = note
        else:
            evidence = norm(rec.get("evidence") or "")
            value = norm(rec.get("value") or "")
            rec["evidence_found"] = bool(evidence) and evidence in full_text
            rec["value_in_evidence"] = bool(value) and value in evidence


def main():
    """
    Re-check saved results without calling Gemini and print the outcome for every record.

        python F_verify.py PMC12217702

    Reads extracted/<model>/<PMCID>.json and structured_json/<PMCID>.json. Nothing is changed.
    Try it: edit one digit of a value in the extracted file, run this again, and that record
    should flip to NOT VERIFIED.
    """
    import json
    import sys
    from pathlib import Path

    ids = sys.argv[1:]

    if not ids:
        sys.exit("Usage: python F_verify.py PMC12217702 [more PMC IDs]")

    folders = [p for p in Path("extracted").glob("*") if p.is_dir()]
    if len(folders) != 1:
        sys.exit(f"Expected exactly one folder inside extracted/, found {[str(f) for f in folders]}")

    for pmcid in ids:
        result_path = folders[0] / f"{pmcid}.json"
        paper_path = Path("structured_json") / f"{pmcid}.json"
        # print(paper_path)
        if not result_path.exists() or not paper_path.exists():
            print(f"[MISSING] {pmcid}: need both {result_path} and {paper_path}")
            continue
        result = json.loads(result_path.read_text(encoding="utf-8"))
        paper = json.loads(paper_path.read_text(encoding="utf-8"))
        check_records(result, paper)

        print(f"\n{pmcid}: {len(result['records'])} records")
        for rec in result["records"]:
            ok = rec.get("evidence_found") and rec.get("value_in_evidence")
            where = rec.get("table") if rec.get("source") == "table" else "text"
            line = f"  {'VERIFIED    ' if ok else 'NOT VERIFIED'} {rec.get('property')}: {rec.get('value')} {rec.get('unit') or ''} [{where}]"
            if rec.get("check_note"):
                line += f"  <- {rec['check_note']}"
            print(line)


if __name__ == "__main__":
    main()

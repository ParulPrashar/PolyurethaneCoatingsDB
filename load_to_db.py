"""
load_to_db.py

Loads extracted/<model>/*.json into SQLite (pu_coatings.db).
Load from extracted/, NOT from llm_raw/ (llm_raw has no verification flags).

Usage:
    python load_to_db.py                      # if only one model folder exists
    python load_to_db.py extracted/<folder>
Safe to re-run: a paper's old rows are replaced.
"""

import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = "pu_coatings.db"
STRUCTURED_DIR = Path("structured_json")
EXTRACTED_ROOT = Path("extracted")

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    pmcid TEXT PRIMARY KEY,
    title TEXT,
    relevant INTEGER NOT NULL,
    model TEXT,
    properties_not_found TEXT,
    extracted_at TEXT
);
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pmcid TEXT NOT NULL REFERENCES papers(pmcid),
    property TEXT NOT NULL,
    sample TEXT,
    value_text TEXT,
    value_num REAL,
    unit TEXT,
    condition_text TEXT,
    evidence TEXT,
    section_title TEXT,
    verified INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_records_property ON records(property);
CREATE INDEX IF NOT EXISTS idx_records_pmcid ON records(pmcid);
"""


def to_number(text):
    """First number in a string like '25.3 ± 1.2' -> 25.3. None if there is none."""
    match = re.search(r"-?\d+(?:\.\d+)?", text or "")
    return float(match.group()) if match else None


def pick_folder():
    if len(sys.argv) > 1:
        return Path(sys.argv[1])
    folders = [p for p in EXTRACTED_ROOT.glob("*") if p.is_dir()]
    if len(folders) == 1:
        return folders[0]
    sys.exit(f"Pass a folder: python load_to_db.py extracted/<name>. Found: {[str(f) for f in folders]}")


def load_paper(conn, json_path):
    data = json.loads(json_path.read_text(encoding="utf-8"))
    pmcid = data["pmcid"]

    title = ""
    structured_path = STRUCTURED_DIR / f"{pmcid}.json"
    if structured_path.exists():
        title = json.loads(structured_path.read_text(encoding="utf-8")).get("title", "")

    conn.execute("DELETE FROM records WHERE pmcid = ?", (pmcid,))
    conn.execute(
        "INSERT OR REPLACE INTO papers VALUES (?, ?, ?, ?, ?, ?)",
        (
            pmcid,
            title,
            1 if data.get("relevant") else 0,
            data.get("model"),
            json.dumps(data.get("properties_not_found", [])),
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )

    count = 0
    for rec in data.get("records", []):
        if not isinstance(rec, dict):
            continue
        value_text = None if rec.get("value") is None else str(rec["value"])
        verified = 1 if rec.get("evidence_found") and rec.get("value_in_evidence") else 0
        conn.execute(
            """INSERT INTO records
               (pmcid, property, sample, value_text, value_num, unit,
                condition_text, evidence, section_title, verified)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pmcid,
                (rec.get("property") or "").strip().lower(),
                rec.get("sample"),
                value_text,
                to_number(value_text),
                rec.get("unit"),
                rec.get("condition"),
                rec.get("evidence"),
                rec.get("section_title"),
                verified,
            ),
        )
        count += 1
    return count


def main():
    folder = pick_folder()
    files = sorted(folder.glob("*.json"))
    if not files:
        sys.exit(f"No JSON files in {folder}")

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    with conn:
        for path in files:
            n = load_paper(conn, path)
            print(f"[OK] {path.stem}: {n} records")

    print("\nDatabase summary:")
    print("papers:", conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0])
    for prop, total, ok in conn.execute(
        "SELECT property, COUNT(*), SUM(verified) FROM records GROUP BY property ORDER BY 2 DESC"
    ):
        print(f"  {prop}: {total} records, {ok} verified")
    conn.close()


if __name__ == "__main__":
    main()

"""
app.py  --  API and website for the PU coatings property database.

Run:    python app.py        then open  http://127.0.0.1:8000
Needs:  pu_coatings.db (made by load_to_db.py) in the same folder as this file,
        and the web/ folder next to it.

The API only reads the database; it never writes to it.
"""

import json
import sqlite3
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "pu_coatings.db"
WEB_DIR = BASE_DIR / "web"

app = FastAPI(
    title="PU Coatings Property Database",
    description="Property values for polyurethane coatings, extracted from open-access papers.",
)

DEGREE_WORDS = {"degree", "degrees", "deg", "deg.", "°"}


def clean_unit(unit):
    """Make unit spelling consistent for display (the model sometimes writes 'degree')."""
    if unit is None:
        return None
    unit = unit.strip()
    if unit.lower() in DEGREE_WORDS:
        return "°"
    return unit or None


def connect():
    """
    Open the database read-only. as_uri() percent-encodes spaces and other characters, so
    folder names like "Parul Prashar" work. If the URI form is ever refused, fall back to a
    normal open (this app only ever runs SELECT queries either way).
    """
    try:
        return sqlite3.connect(f"{DB_PATH.as_uri()}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return sqlite3.connect(str(DB_PATH))


def run_query(sql, params=()):
    """Open the database read-only, run one query, return a list of dicts."""
    if not DB_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail="Database not found. Run load_to_db.py first, then restart app.py.",
        )
    conn = connect()
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    except sqlite3.OperationalError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Database problem ({e}). Run load_to_db.py, then restart app.py.",
        )
    finally:
        conn.close()


@app.get("/api/summary")
def summary():
    """Headline numbers for the top of the page."""
    row = run_query(
        """
        SELECT
            (SELECT COUNT(*) FROM papers)                          AS papers,
            (SELECT COUNT(DISTINCT pmcid) FROM records)            AS papers_with_values,
            (SELECT COUNT(*) FROM records)                         AS records,
            (SELECT COALESCE(SUM(verified), 0) FROM records)       AS verified_records
        """
    )[0]
    row["models"] = [
        r["model"] for r in run_query("SELECT DISTINCT model FROM papers WHERE model IS NOT NULL")
    ]
    return row


@app.get("/api/properties")
def properties():
    """Each property with how many values (and how many verified) it has."""
    return run_query(
        """
        SELECT property,
               COUNT(*)                     AS total,
               COALESCE(SUM(verified), 0)   AS verified,
               COUNT(DISTINCT pmcid)        AS papers
        FROM records
        GROUP BY property
        ORDER BY total DESC, property
        """
    )


@app.get("/api/records")
def records(
    prop: Optional[str] = Query(None, alias="property"),
    verified_only: bool = False,
    limit: int = Query(500, ge=1, le=2000),
):
    """Values, optionally filtered by property and by verified status."""
    sql = """
        SELECT r.id, r.pmcid, p.title AS paper_title, r.property, r.sample,
               r.value_text, r.value_num, r.unit, r.condition_text,
               r.evidence, r.section_title, r.verified
        FROM records r
        JOIN papers p ON p.pmcid = r.pmcid
    """
    where, params = [], []
    if prop:
        where.append("r.property = ?")
        params.append(prop.strip().lower())
    if verified_only:
        where.append("r.verified = 1")
    if where:
        sql += " WHERE " + " AND ".join(where)  # fixed text only; all values use ? placeholders
    sql += " ORDER BY r.pmcid, r.id LIMIT ?"
    params.append(limit)

    rows = run_query(sql, params)
    for row in rows:
        row["condition"] = row.pop("condition_text")
        row["unit"] = clean_unit(row["unit"])
        row["verified"] = bool(row["verified"])
    return rows


@app.get("/api/papers")
def papers():
    """Every paper that was read, with how many values it produced."""
    rows = run_query(
        """
        SELECT p.pmcid, p.title, p.relevant, p.model, p.properties_not_found,
               COUNT(r.id)                  AS records,
               COALESCE(SUM(r.verified), 0) AS verified
        FROM papers p
        LEFT JOIN records r ON r.pmcid = p.pmcid
        GROUP BY p.pmcid
        ORDER BY p.pmcid
        """
    )
    for row in rows:
        row["relevant"] = bool(row["relevant"])
        try:
            row["properties_not_found"] = json.loads(row["properties_not_found"] or "[]")
        except json.JSONDecodeError:
            row["properties_not_found"] = []
    return rows


# The website. This must come AFTER the /api routes so it does not swallow them.
if not WEB_DIR.is_dir():
    raise SystemExit(f"Folder not found: {WEB_DIR}. The web/ folder must sit next to app.py.")
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")


if __name__ == "__main__":
    print("Open http://127.0.0.1:8000 in your browser. Press Ctrl+C to stop.")
    uvicorn.run(app, host="127.0.0.1", port=8000)

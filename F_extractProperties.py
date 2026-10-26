"""
F_extractProperties.py

Sends each structured paper JSON (structured_json/) to a Gemini model with the extraction
prompt, saves the raw reply, parses it, checks every record against the paper (see
F_verify.py), and writes the result to extracted/<model>/<PMCID>.json.

Papers that already have an output file are skipped, so no Gemini call is made for them.

Usage:
    python F_extractProperties.py PMC12217702     # one or more PMC IDs
    python F_extractProperties.py                 # every paper not done yet
"""

import json
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from google import genai
from google.genai import types

from F_verify import check_records

API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("GEMINI_MODEL")
if not API_KEY or not MODEL:
    sys.exit("Set GEMINI_API_KEY and GEMINI_MODEL in your .env file first.")

client = genai.Client(api_key=API_KEY)

STRUCTURED_DIR = Path("structured_json")
MODEL_TAG = re.sub(r"[^\w.-]", "_", MODEL)
OUTPUT_DIR = Path("extracted") / MODEL_TAG
RAW_DIR = Path("llm_raw") / MODEL_TAG
PAUSE_SECONDS = 12  # free-tier friendly pause, used only after a real Gemini call

SYSTEM_PROMPT = """You are extracting measured property values from ONE scientific paper about polyurethane (PU) coatings. Your output goes into a database, so accuracy matters more than completeness: it is better to leave a value out than to guess.

THE INPUT
The paper is JSON with:
- "title" and "abstract" (a list of paragraphs)
- "body": sections, each with "title", "paragraphs" and "subsections"
- "tables": a list of tables. Each has "label" (for example "Table 2"), "caption", "headers" (one or two header rows), "rows" (each row is a list of cell strings) and "footnotes".
A marker like "[Table 2 is in the tables list]" in the body shows where a table sat in the text.

STEP 1: RELEVANCE
Set "relevant" to true only if the coatings or films that this paper itself prepares or tests are waterborne polyurethane (WPU, aqueous PU dispersion or emulsion, including hybrids such as PU-acrylate). Otherwise set it to false. For example, it is false if the PU is solvent-borne and the waterborne coating is acrylic, or if PU is only mentioned as background. Write one short sentence in "relevance_note" saying why. If false, return an empty "records" list and put all five property names in "properties_not_found".

STEP 2: THE FIVE PROPERTIES
Extract only these, using exactly these names in "property":
- "tensile strength" (also called ultimate tensile strength, stress at break, tensile stress; usually MPa)
- "elongation at break" (also called strain at break, elongation; usually %)
- "water contact angle" (also WCA, contact angle with water; degrees)
- "water absorption" (also water uptake, swelling in water; usually %)
- "glass transition temperature" (also Tg, T_g, glass transition; usually degrees C). If the paper gives separate values for soft and hard segments, say which in "condition".
Look for synonyms, abbreviations and symbols. Do NOT extract anything else, such as Young's modulus, hardness, adhesion, gloss, antimicrobial results, color change, solids content, thickness or particle size.

WHAT COUNTS
- Only values this paper measured on its own coating or film samples.
- Ignore values quoted from other papers (introduction, literature comparisons, tables with a reference column), properties of the substrate (wood, fabric, glass; for example the moisture content of wood), and values of other properties that merely sound similar.
- Extract EVERY such value, for every sample and every condition (different compositions, loadings, curing, aging, washing, abrasion), not only the best or final one. Each is its own record.
- Report each value once. If the same value is in the abstract and in the results, use the results. If it is in both a sentence and a table, use the sentence; table rows that the sentence does not give still get their own records.

NO INVENTING
- Use only what is written in the JSON. Never guess, estimate, calculate, average or convert.
- "value": copy exactly as written, without the unit, including any plus-minus part or qualifier (for example "25.3 ± 1.2", "~140", "over 90").
- "unit": as written, either in the sentence or in the column header (a header "Strength (MPa)" gives "MPa"). If no unit appears anywhere, use null.
- "sample": the name the paper uses, taken from the same sentence or table row as the value.
- "condition": what makes this value specific (test temperature, time, loading, before or after curing, aging, and so on). It must come from the same sentence or the same table row or column. Never borrow a condition from another sentence, another table or another property. If the paper states none, use null. If a sentence gives a "from X to Y" pair, make one record for each value and describe each using the paper's own words (for example "initial formulation" and "optimized formulation").

VALUES FROM TEXT ("source": "text")
- "evidence": the complete sentence containing the value, copied exactly, character for character. Never shorten, reword or join sentences.
- "section_title": the title of the section the sentence is in, or "Abstract".
- "table" and "column": null.

VALUES FROM TABLES ("source": "table")
- "table": the table's label, for example "Table 3".
- Find the cell where the sample's row and the property's column cross. The header rows name the columns. A header cell can repeat across several columns when it spans them.
- "column": the column's header text copied exactly from "headers". If there are two header rows, join the two cells with " / ", top row first (for example "Aging Process / Control % Reduction").
- "evidence": the cells of that row copied exactly, in order, joined with " | ", including empty cells.
- "section_title": null.
- If a row has fewer cells than the header, a merged cell from the row above is missing at the left. Line the row's cells up from the right end of the header row.
- Skip cells that are empty or contain "-", "n/a" or "nd".

NOTHING FOUND
If a property has no value in the paper, create NO record for it. Only list its name in "properties_not_found". Never create a record whose value is null.

BEFORE YOU ANSWER
Check every record. The value must appear in the evidence. The unit, sample and condition must come from the same sentence or row (or its column header). The value must belong to one of the five properties and to this paper's own coating samples. Delete any record that fails a check.

Return ONLY valid JSON, with no other text and no markdown, in this shape. The two example records only show the format; they are not real data:
{"pmcid": "", "relevant": true, "relevance_note": "", "records": [
{"property": "tensile strength", "sample": "Sample A", "value": "12.5", "unit": "MPa", "condition": "after 7 days curing", "source": "text", "table": null, "column": null, "evidence": "The tensile strength of Sample A after 7 days curing was 12.5 MPa.", "section_title": "3.2. Mechanical properties"},
{"property": "water absorption", "sample": "Sample B", "value": "4.1", "unit": "%", "condition": "24 h", "source": "table", "table": "Table 2", "column": "Water uptake (%) / 24 h", "evidence": "Sample B | 4.1 | 6.3", "section_title": null}
], "properties_not_found": []}
"""


def call_model(paper):
    """Send one paper to the model. Retries 3 times. Returns (reply_text, (tokens_in, tokens_out))."""
    user_content = json.dumps(paper, ensure_ascii=False)
    last_error = None
    for attempt in range(1, 4):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=user_content,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                ),
            )
            usage = response.usage_metadata
            return response.text, (
                usage.prompt_token_count or 0,
                usage.candidates_token_count or 0,
            )
        except Exception as e:
            code = getattr(e, "code", None)
            if code and 400 <= code < 500 and code != 429:
                raise  # bad model name, bad key, bad request: retrying will not help
            last_error = e
            wait = 15 * attempt
            print(f"  attempt {attempt} failed: {e} -- retrying in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"API failed after 3 attempts: {last_error}")


def process(pmcid):
    """
    Extract one paper. Returns what happened:
      "skipped" - an output file already exists (no Gemini call was made)
      "missing" - there is no structured_json file for this PMC ID
      "failed"  - Gemini answered, but the reply was unusable
      "ok"      - extracted and saved
    """
    in_path = STRUCTURED_DIR / f"{pmcid}.json"
    out_path = OUTPUT_DIR / f"{pmcid}.json"

    if out_path.exists():
        print(f"[SKIP] {pmcid}: already extracted")
        return "skipped"
    if not in_path.exists():
        print(f"[MISSING] {in_path} not found")
        return "missing"

    paper = json.loads(in_path.read_text(encoding="utf-8"))
    print(f"[RUN] {pmcid} with {MODEL}")
    text, usage = call_model(paper)

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / f"{pmcid}.json").write_text(text, encoding="utf-8")

    try:
        result = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"[BAD JSON] {pmcid}: {e} (raw reply saved in {RAW_DIR})")
        return "failed"

    if (
        not isinstance(result, dict)
        or not isinstance(result.get("records"), list)
        or not isinstance(result.get("properties_not_found"), list)
    ):
        print(f"[BAD SHAPE] {pmcid}: reply is JSON but not the expected shape")
        return "failed"

    # Drop records that have no value (the prompt forbids them, but a model can slip).
    result["records"] = [
        r for r in result["records"]
        if isinstance(r, dict) and r.get("value") not in (None, "")
    ]

    # The code knows the real PMC ID; do not trust the model with it.
    if result.get("pmcid") != pmcid:
        print(f"  note: model returned pmcid {result.get('pmcid')!r}, overwriting")
        result["pmcid"] = pmcid

    check_records(result, paper)
    result["model"] = MODEL

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    flagged = sum(
        1 for r in result["records"]
        if not (r.get("evidence_found") and r.get("value_in_evidence"))
    )
    print(
        f"[OK] {pmcid}: relevant={result.get('relevant')}, "
        f"{len(result['records'])} records, {flagged} flagged, "
        f"not found: {result['properties_not_found']}, "
        f"tokens in/out: {usage[0]}/{usage[1]}"
    )
    return "ok"


def main():
    ids = sys.argv[1:] or sorted(p.stem for p in STRUCTURED_DIR.glob("*.json"))
    counts = {"ok": 0, "skipped": 0, "missing": 0, "failed": 0, "error": 0}

    for pmcid in ids:
        try:
            status = process(pmcid)
        except Exception as e:
            print(f"[ERROR] {pmcid}: {e}")
            status = "error"
        counts[status] += 1

        # Wait only after a real Gemini call, never after a skipped paper.
        if status in ("ok", "failed", "error"):
            time.sleep(PAUSE_SECONDS)

    print(
        f"done: {counts['ok']} extracted, {counts['skipped']} skipped, "
        f"{counts['failed']} unusable replies, {counts['error']} errors, "
        f"{counts['missing']} missing"
    )


if __name__ == "__main__":
    main()

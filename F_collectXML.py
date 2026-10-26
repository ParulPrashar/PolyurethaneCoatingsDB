"""
F_collectXML.py

Search Europe PMC for papers, collect PMC IDs, and save each paper's
full-text JATS XML into raw_xml/. This is the drop-in replacement for
F_saveRawXML.py (that file is left as-is).

Uses the JSON search API so we read named fields (pmcid) instead of
fragile XML child indexes.

Usage:
    python F_collectXML.py
    python F_collectXML.py --max 10
    python F_collectXML.py --query "waterborne polyurethane coating"
    python F_collectXML.py PMC13567686 PMC12217702
"""

import argparse
import sys
import time
from pathlib import Path

import requests

SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
FULLTEXT_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
DEFAULT_QUERY = "waterborne polyurethane"
OUTPUT_DIR = Path("raw_xml")
PAGE_SIZE = 25
REQUEST_TIMEOUT = 30
PAUSE_BETWEEN_DOWNLOADS = 0.4


def search_pmcids(query, max_papers):
    """
    Page through Europe PMC search results and yield unique PMCIDs.
    Stops after max_papers IDs, or when the search has no more pages.
    """
    seen = set()
    cursor = "*"

    while True:
        try:
            response = requests.get(
                SEARCH_URL,
                params={
                    "query": query,
                    "format": "json",
                    "pageSize": PAGE_SIZE,
                    "cursorMark": cursor,
                    "resultType": "lite",
                },
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.RequestException as e:
            print(f"[ERROR] search failed: {e}")
            break
        except ValueError as e:
            print(f"[ERROR] search did not return JSON: {e}")
            break

        results = data.get("resultList", {}).get("result", []) or []
        if not results:
            break

        for item in results:
            pmcid = item.get("pmcid") or ""
            if not pmcid.startswith("PMC"):
                continue
            if pmcid in seen:
                continue
            seen.add(pmcid)
            yield pmcid
            if max_papers is not None and len(seen) >= max_papers:
                return

        next_cursor = data.get("nextCursorMark")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor


def download_xml(pmcid):
    """
    Fetch one paper's full-text XML.
    Returns (status, message):
        saved | skipped | no_fulltext | error
    """
    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / f"{pmcid}.xml"

    if out_path.exists() and out_path.stat().st_size > 0:
        return "skipped", f"[SKIP] {pmcid}: already in {out_path}"

    url = FULLTEXT_URL.format(pmcid=pmcid)
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
    except requests.exceptions.RequestException as e:
        return "error", f"[ERROR] {pmcid}: {e}"

    if response.status_code == 404:
        return "no_fulltext", f"[NO XML] {pmcid}: Europe PMC has no fullTextXML"
    if response.status_code != 200:
        return "error", f"[ERROR] {pmcid}: HTTP {response.status_code}"

    content = response.content
    if not content.strip().startswith(b"<"):
        return "error", f"[ERROR] {pmcid}: response was not XML"

    out_path.write_bytes(content)
    return "saved", f"[OK] {pmcid} -> {out_path}"


def collect(pmcids):
    counts = {"saved": 0, "skipped": 0, "no_fulltext": 0, "error": 0}
    for i, pmcid in enumerate(pmcids):
        status, message = download_xml(pmcid)
        counts[status] += 1
        print(message)
        if i < len(pmcids) - 1:
            time.sleep(PAUSE_BETWEEN_DOWNLOADS)

    print(
        f"done: {counts['saved']} saved, {counts['skipped']} skipped, "
        f"{counts['no_fulltext']} without full text, {counts['error']} errors"
    )
    return counts


def parse_args():
    parser = argparse.ArgumentParser(
        description="Download Europe PMC full-text XML into raw_xml/."
    )
    parser.add_argument(
        "pmcids",
        nargs="*",
        help="Optional PMC IDs to download (skips the search).",
    )
    parser.add_argument(
        "--query",
        default=DEFAULT_QUERY,
        help=f'Search query (default: "{DEFAULT_QUERY}")',
    )
    parser.add_argument(
        "--max",
        type=int,
        default=25,
        help="Max new PMC IDs to take from search (default: 25). Use 0 for all.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.pmcids:
        pmcids = []
        for raw in args.pmcids:
            pmcid = raw.strip().upper()
            if not pmcid.startswith("PMC"):
                pmcid = "PMC" + pmcid
            pmcids.append(pmcid)
        print(f"downloading {len(pmcids)} given PMC ID(s)")
    else:
        max_papers = None if args.max == 0 else args.max
        limit_note = "all hits" if max_papers is None else f"up to {max_papers}"
        print(f'searching Europe PMC for {args.query!r} ({limit_note})')
        pmcids = list(search_pmcids(args.query, max_papers))
        print(f"found {len(pmcids)} PMC ID(s)")

    if not pmcids:
        print("nothing to download")
        return 1

    counts = collect(pmcids)
    return 1 if counts["error"] and not counts["saved"] and not counts["skipped"] else 0


if __name__ == "__main__":
    sys.exit(main())

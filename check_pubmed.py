#!/usr/bin/env python3
"""
Validates paper data from a CSV file against the PubMed API.
Usage: python check_pubmed.py <file.csv>

CSV structure:
  Row 1: ignored
  Row 2: headers
  Row 3: explanations (ignored)
  Row 4+: data
"""

import csv
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
DELAY_BETWEEN_REQUESTS = 0.4  # seconds (NCBI rate limit: ~3 req/s without API key)

# Fields to compare: (CSV column, PubMed key, display label)
FIELDS = [
    ("Título",         "title",        "Title"),
    ("Primer Autor",   "first_author", "First Author"),
    ("Revista",        "journal",      "Journal"),
    ("Año",            "year",         "Year"),
    ("Volumen",        "volume",       "Volume"),
    ("Primera Página", "first_page",   "First Page"),
    ("Ultima Página",  "last_page",    "Last Page"),
]


def normalize(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace (for accent- and case-insensitive comparisons)."""
    nfkd = unicodedata.normalize("NFKD", text.strip().lower())
    return " ".join(nfkd.encode("ascii", "ignore").decode("ascii").split())


def fetch_pubmed(pmid: str) -> dict | None:
    """Query the PubMed API and return article metadata, or None on failure."""
    params = {"db": "pubmed", "id": pmid, "retmode": "xml", "rettype": "abstract"}
    try:
        resp = requests.get(EFETCH_URL, params=params, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
    except (requests.RequestException, ET.ParseError) as exc:
        print(f"  [ERROR] {exc}")
        return None

    article = root.find(".//PubmedArticle")
    if article is None:
        return None

    def text(xpath):
        """Extract text from an XML node; return empty string if the node does not exist."""
        el = article.find(xpath)
        return el.text.strip() if el is not None and el.text else ""

    # Build author list
    authors = []
    for a in article.findall(".//AuthorList/Author"):
        last = a.findtext("LastName", "").strip()
        forename = a.findtext("ForeName", "").strip() or a.findtext("Initials", "").strip()
        if last:
            authors.append(f"{last} {forename}".strip())
        elif collective := a.findtext("CollectiveName", "").strip():
            authors.append(collective)

    # PubMed abbreviates page ranges (e.g. "123-8"); expand to full range ("123-128")
    first_page, last_page = "", ""
    medline_pgn = text(".//MedlinePgn")
    if medline_pgn:
        parts = medline_pgn.split("-")
        first_page = parts[0].strip()
        if len(parts) > 1:
            end = parts[1].strip()
            if len(end) < len(first_page):
                end = first_page[: len(first_page) - len(end)] + end
            last_page = end

    return {
        "title":        text(".//ArticleTitle"),
        "first_author": authors[0] if authors else "",
        "journal":      text(".//Journal/Title") or text(".//MedlineJournalInfo/MedlineTA"),
        "year":         text(".//Journal/JournalIssue/PubDate/Year")
                        or text(".//Journal/JournalIssue/PubDate/MedlineDate")[:4],
        "volume":       text(".//Journal/JournalIssue/Volume"),
        "first_page":   first_page,
        "last_page":    last_page,
    }


def main(csv_path: str):
    path = Path(csv_path)
    if not path.exists():
        sys.exit(f"Error: file not found: '{csv_path}'")

    # Auto-detect delimiter (comma, semicolon, or tab)
    with open(path, newline="", encoding="utf-8-sig") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        all_rows = list(csv.reader(fh, dialect))

    if len(all_rows) < 4:
        sys.exit("Error: CSV has too few rows (at least 4 expected).")

    # Row 2 (index 1) holds headers; row 3 (index 2) holds explanations and is ignored
    headers   = [h.strip() for h in all_rows[1]]
    data_rows = all_rows[3:]

    # Verify all required columns are present before processing any row
    required_columns = [csv_col for csv_col, _, _ in FIELDS] + ["PMID (PubMed Identifier)"]
    missing = [col for col in required_columns if col not in headers]
    if missing:
        sys.exit(
            "Error: the following required columns are missing from the CSV:\n"
            + "\n".join(f"  - {col}" for col in missing)
        )

    totals = {"ok": 0, "with_diffs": 0, "no_pmid": 0, "not_found": 0}

    print(f"File : {path.name}  |  Data rows: {len(data_rows)}")
    print("=" * 70)

    for i, raw in enumerate(data_rows, start=4):
        # Skip completely empty rows
        if not any(c.strip() for c in raw):
            continue

        row  = dict(zip(headers, raw))
        pmid = row.get("PMID (PubMed Identifier)", "").strip()

        print(f"\nRow {i} | PMID: {pmid or '—'}")

        if not pmid:
            print("  No PMID — skipped")
            totals["no_pmid"] += 1
            continue

        pubmed = fetch_pubmed(pmid)
        time.sleep(DELAY_BETWEEN_REQUESTS)  # respect API rate limit

        if pubmed is None:
            print(f"  PMID {pmid} not found in PubMed")
            totals["not_found"] += 1
            continue

        # Compare each field between the CSV and PubMed (normalized to ignore accents and case)
        diffs = []
        for csv_col, pm_key, label in FIELDS:
            csv_val = row.get(csv_col, "").strip()
            pm_val  = pubmed.get(pm_key, "").strip()
            if normalize(csv_val) != normalize(pm_val):
                print(f"  [!] {label}")
                print(f"        CSV    : {csv_val or '—'}")
                print(f"        PubMed : {pm_val or '—'}")
                diffs.append(label)

        if diffs:
            totals["with_diffs"] += 1
        else:
            print("  OK")
            totals["ok"] += 1

    print("\n" + "=" * 70)
    print("SUMMARY")
    print(f"  No differences  : {totals['ok']}")
    print(f"  With differences: {totals['with_diffs']}")
    print(f"  No PMID         : {totals['no_pmid']}")
    print(f"  Not found       : {totals['not_found']}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python check_pubmed.py <file.csv>")
    main(sys.argv[1])

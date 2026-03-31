#!/usr/bin/env python3
"""
Filters articles from a CSV using PubMed only to retrieve author affiliations.

Workflow:
  1. Read the CSV and filter by year (no API calls).
  2. For each row that passes the year filter, query PubMed by PMID
     to obtain the real author affiliations.
  3. Keep only articles where at least one author is affiliated with
     an institution whose name contains the keyword.

Output: title, authors from the institution, institution name, year,
        open access, impact factor, quartile, and PubMed URL.

Usage:
  python filter_csv.py <file.csv> --year YEAR --keyword WORD
  python filter_csv.py <file.csv> [config.json]
  python filter_csv.py <file.csv> [config.json] --author
"""

import argparse
import csv
import json
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
DELAY_BETWEEN_REQUESTS = 0.4  # seconds (NCBI rate limit: ~3 req/s without API key)
DEFAULT_CONFIG = "filter_config.json"


def normalize(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace (for accent- and case-insensitive comparisons)."""
    nfkd = unicodedata.normalize("NFKD", text.strip().lower())
    return " ".join(nfkd.encode("ascii", "ignore").decode("ascii").split())


def remove_accents(text: str) -> str:
    """Strip accents and normalize hyphens to spaces (used for author name deduplication)."""
    nfkd = unicodedata.normalize("NFKD", text.replace("-", " "))
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def load_csv(path: Path) -> tuple[list[str], list[dict]]:
    """Read the CSV, auto-detect its delimiter, and return headers and non-empty data rows."""
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
    headers = [h.strip() for h in all_rows[1]]
    data_rows = [
        dict(zip(headers, [c.strip() for c in row]))
        for row in all_rows[3:]
        if any(c.strip() for c in row)
    ]
    return headers, data_rows


def parse_args():
    """Parse CLI arguments; fall back to config file when --year is not provided."""
    parser = argparse.ArgumentParser(
        description="Filter articles from a CSV by year and affiliation (via PubMed)."
    )
    parser.add_argument("csv",    help="Path to the CSV file")
    parser.add_argument("config", nargs="?", help="JSON config file (optional)")
    parser.add_argument("--year",    help="Publication year to filter by")
    parser.add_argument("--keyword", default="", help="Institution/hospital keyword")
    parser.add_argument("--debug",  action="store_true", help="Print detected columns and unique year values")
    parser.add_argument("--author", action="store_true", help="Group results by author")

    args = parser.parse_args()

    if args.year:
        return args.csv, args.year.strip(), args.keyword.strip(), args.debug, args.author

    config_path = args.config or DEFAULT_CONFIG
    cfg_file = Path(config_path)
    if not cfg_file.exists():
        sys.exit(f"Error: --year not provided and config file not found: '{config_path}'.")
    with open(cfg_file, encoding="utf-8") as fh:
        config = json.load(fh)
    if "year" not in config:
        sys.exit("Error: missing key 'year' in config file.")
    return args.csv, config["year"].strip(), config.get("affiliation_keyword", "").strip(), args.debug, args.author


def fetch_affiliations(pmid: str) -> list[dict] | None:
    """Return a list of {name, affiliations} dicts for the given PMID, or None on failure."""
    params = {"db": "pubmed", "id": pmid, "retmode": "xml", "rettype": "abstract"}
    try:
        resp = requests.get(EFETCH_URL, params=params, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
    except (requests.RequestException, ET.ParseError) as exc:
        print(f"  [ERROR] PMID {pmid}: {exc}", file=sys.stderr)
        return None

    article = root.find(".//PubmedArticle")
    if article is None:
        return None

    authors = []
    for a in article.findall(".//AuthorList/Author"):
        last = a.findtext("LastName", "").strip()
        forename = a.findtext("ForeName", "").strip() or a.findtext("Initials", "").strip()
        name = f"{last} {forename}".strip() if last else a.findtext("CollectiveName", "").strip()
        affiliations = [
            aff.text.strip()
            for aff in a.findall(".//AffiliationInfo/Affiliation")
            if aff.text
        ]
        if name:
            authors.append({"name": name, "affiliations": affiliations})
    return authors


def matching_authors(authors: list, keyword_norm: str) -> list[dict]:
    """Return name and raw affiliation string for authors whose affiliation contains the keyword."""
    matched = []
    for author in authors:
        for aff in author["affiliations"]:
            if keyword_norm in normalize(aff):
                matched.append({"name": author["name"], "affiliation": aff})
                break  # one matching affiliation per author is enough
    return matched


def main():
    csv_path_str, target_year, keyword, debug, author_mode = parse_args()

    path = Path(csv_path_str)
    if not path.exists():
        sys.exit(f"Error: file not found: '{csv_path_str}'")

    headers, rows = load_csv(path)
    keyword_norm = normalize(keyword)

    center_filter = f"  |  institution contains='{keyword}'" if keyword else ""
    print(f"File    : {path.name}  |  Data rows: {len(rows)}")
    print(f"Filters : year={target_year}{center_filter}")
    print("=" * 70)

    # Detect the Impact Factor column dynamically
    if_columns = [h for h in headers if h.startswith("Impact Factor")]
    if len(if_columns) > 1:
        sys.exit(
            f"Error: found multiple Impact Factor columns: {if_columns}. "
            "Please keep only one."
        )
    if_col = if_columns[0] if if_columns else None

    if debug:
        print("\n[DEBUG] Detected columns:")
        for i, h in enumerate(headers):
            print(f"  {i:2d}: {repr(h)}")
        print(f"\n[DEBUG] Impact Factor column: {repr(if_col)}")
        years = sorted({r.get("Año", "").strip() for r in rows} - {""})
        print(f"\n[DEBUG] Unique values in 'Año': {years}")
        print()

    # Step 1: filter by year from the CSV (no API calls)
    year_filtered = [r for r in rows if r.get("Año", "").strip() == target_year]
    print(f"Rows with year {target_year}: {len(year_filtered)}")
    if keyword:
        print(f"Querying PubMed for {len(year_filtered)} article(s)...\n")

    results = []
    for row in year_filtered:
        pmid = row.get("PMID (PubMed Identifier)", "").strip()

        if keyword:
            # Step 2: when a keyword is given, verify affiliation via PubMed
            if not pmid:
                print(f"  [SKIPPED] No PMID: {row.get('Título', '')[:60]}", file=sys.stderr)
                continue
            authors = fetch_affiliations(pmid)
            time.sleep(DELAY_BETWEEN_REQUESTS)  # respect API rate limit
            if authors is None:
                print(f"  [SKIPPED] PMID {pmid} not found in PubMed", file=sys.stderr)
                continue
            matched = matching_authors(authors, keyword_norm)
            if not matched:
                continue
            authors_list = [m["name"] for m in matched]
            authors_str  = "; ".join(authors_list)
            center_str   = matched[0]["affiliation"]
        else:
            # No keyword: use authors and department directly from the CSV
            authors_str  = _format_authors(row)
            authors_list = _parse_authors(row)
            center_str   = row.get("Servicio al que pertenece en el HUGCDN", "—").strip() or "—"

        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "—"
        results.append({
            "title":        row.get("Título", "—").strip() or "—",
            "authors":      authors_str or "—",
            "authors_list": authors_list,
            "center":       center_str or "—",
            "year":         row.get("Año", "—").strip() or "—",
            "oa":           row.get("Open Access", "—").strip() or "—",
            "if":           (row.get(if_col, "—").strip() or "—") if if_col else "—",
            "quartile":     row.get("Cuartil", "—").strip() or "—",
            "url":          url,
        })

    print(f"\nArticles matching filters: {len(results)}\n")

    if author_mode:
        _print_by_author(results)
    else:
        for n, r in enumerate(results, start=1):
            print(f"{n}. {r['title']}")
            print(f"   Institution authors : {r['authors']}")
            print(f"   Institution         : {r['center']}")
            print(f"   Year                : {r['year']}")
            print(f"   Open Access         : {r['oa']}")
            print(f"   Impact Factor       : {r['if']}")
            print(f"   Quartile            : {r['quartile']}")
            print(f"   PubMed URL          : {r['url']}")
            print()


def _format_authors(row: dict) -> str:
    """Combine 'Primer Autor' and 'Resto de Autores' CSV columns into a semicolon-separated string."""
    parts = []
    first = row.get("Primer Autor", "").strip()
    rest  = row.get("Resto de Autores", "").strip()
    if first:
        parts.append(first)
    if rest:
        parts.append(rest)
    return "; ".join(parts) if parts else "—"


def _parse_authors(row: dict) -> list[str]:
    """Return a list of author names from the CSV columns."""
    authors = []
    first = row.get("Primer Autor", "").strip()
    rest  = row.get("Resto de Autores", "").strip()
    if first:
        authors.append(first)
    if rest:
        authors.extend([a.strip() for a in rest.split(";") if a.strip()])
    return authors


def _print_by_author(results: list[dict]) -> None:
    """Print articles grouped by author."""
    # Build index: author name (accent-free) -> list of articles
    author_index: dict[str, list[dict]] = {}
    for r in results:
        for author in r["authors_list"]:
            key = remove_accents(author)
            author_index.setdefault(key, []).append(r)

    print(f"Authors with publications: {len(author_index)}\n")
    print("=" * 70)
    for author in sorted(author_index, key=lambda a: a.lower()):
        articles = author_index[author]
        print(f"\nAUTHOR: {author}  ({len(articles)} article(s))")
        print("-" * 60)
        for n, r in enumerate(articles, start=1):
            print(f"  {n}. {r['title']}")
            print(f"     IF: {r['if']}  |  Quartile: {r['quartile']}  |  Open Access: {r['oa']}")
            print(f"     URL: {r['url']}")
        print()


if __name__ == "__main__":
    main()

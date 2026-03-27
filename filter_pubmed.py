#!/usr/bin/env python3
"""
Filters articles from a CSV by publication year in PubMed and author affiliation.

Usage: python filter_pubmed.py <file.csv> [config.json]

The config file (default: filter_config.json) must contain:
  {
    "year": "2024",
    "affiliation_keyword": "Hospital"
  }

Only articles that meet both conditions are returned:
  - The year in PubMed matches the configured year.
  - At least one author is affiliated with an institution whose name contains
    the keyword (case- and accent-insensitive).
"""

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


def load_config(config_path: str) -> dict:
    """Load and validate the JSON config file; exit with an error if required keys are missing."""
    path = Path(config_path)
    if not path.exists():
        sys.exit(f"Error: config file not found: '{config_path}'")
    with open(path, encoding="utf-8") as fh:
        config = json.load(fh)
    for key in ("year", "affiliation_keyword"):
        if key not in config:
            sys.exit(f"Error: missing key '{key}' in config file.")
    return config


def fetch_pubmed(pmid: str) -> dict | None:
    """Query the PubMed API and return title, DOI, year, and authors with affiliations, or None on failure."""
    params = {"db": "pubmed", "id": pmid, "retmode": "xml", "rettype": "abstract"}
    try:
        resp = requests.get(EFETCH_URL, params=params, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
    except (requests.RequestException, ET.ParseError) as exc:
        print(f"  [ERROR] {exc}", file=sys.stderr)
        return None

    article = root.find(".//PubmedArticle")
    if article is None:
        return None

    def text(xpath):
        """Extract text from an XML node; return empty string if the node does not exist."""
        el = article.find(xpath)
        return el.text.strip() if el is not None and el.text else ""

    # Publication year (fall back to the first 4 chars of MedlineDate when Year is absent)
    year = (
        text(".//Journal/JournalIssue/PubDate/Year")
        or text(".//Journal/JournalIssue/PubDate/MedlineDate")[:4]
    )

    # DOI
    doi = ""
    for article_id in article.findall(".//ArticleIdList/ArticleId"):
        if article_id.get("IdType") == "doi":
            doi = article_id.text.strip() if article_id.text else ""
            break

    # Title
    title = text(".//ArticleTitle")

    # Authors with their affiliations
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

    return {
        "title":   title,
        "doi":     doi,
        "year":    year,
        "authors": authors,
    }


def matching_authors(authors: list, keyword_normalized: str) -> list[dict]:
    """Return name and raw affiliation string for authors whose affiliation contains the keyword."""
    matched = []
    for author in authors:
        for aff in author["affiliations"]:
            if keyword_normalized in normalize(aff):
                matched.append({"name": author["name"], "affiliation": aff})
                break
    return matched


def main(csv_path: str, config_path: str):
    config = load_config(config_path)
    target_year        = config["year"].strip()
    keyword_normalized = normalize(config["affiliation_keyword"])

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

    print(f"File    : {path.name}  |  Data rows: {len(data_rows)}")
    print(f"Filters : year={target_year}  |  institution contains='{config['affiliation_keyword']}'")
    print("=" * 70)

    results = []

    for i, raw in enumerate(data_rows, start=4):
        # Skip completely empty rows
        if not any(c.strip() for c in raw):
            continue

        row  = dict(zip(headers, raw))
        pmid = row.get("PMID (PubMed Identifier)", "").strip()

        # Cannot query PubMed without a PMID
        if not pmid:
            continue

        pubmed = fetch_pubmed(pmid)
        time.sleep(DELAY_BETWEEN_REQUESTS)  # respect API rate limit

        if pubmed is None:
            print(f"  [SKIPPED] PMID {pmid} not found in PubMed", file=sys.stderr)
            continue

        # Filter 1: year in PubMed must match the configured year
        if pubmed["year"] != target_year:
            continue

        # Filter 2: at least one author must belong to the target institution
        matched = matching_authors(pubmed["authors"], keyword_normalized)
        if not matched:
            continue

        results.append({
            "title":   pubmed["title"],
            "doi":     pubmed["doi"] or "—",
            "url":     f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "matched": matched,
        })

    # Print results
    print(f"\nArticles matching filters: {len(results)}\n")
    for n, r in enumerate(results, start=1):
        print(f"{n}. {r['title']}")
        print(f"   DOI         : {r['doi']}")
        print(f"   URL         : {r['url']}")
        for m in r["matched"]:
            print(f"   Author      : {m['name']}")
            print(f"   Institution : {m['affiliation']}")
        print()


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        sys.exit("Usage: python filter_pubmed.py <file.csv> [config.json]")
    config_file = sys.argv[2] if len(sys.argv) == 3 else DEFAULT_CONFIG
    main(sys.argv[1], config_file)

#!/usr/bin/env python3
"""
Filtra artículos de un CSV según año de publicación en PubMed y centro del autor.

Uso: python filter_pubmed.py <archivo.csv> [config.json]

El archivo de configuración (por defecto filter_config_example.json) debe contener:
  {
    "year": "2024",
    "affiliation_keyword": "Hospital"
  }

Se devuelven solo los artículos en los que:
  - El año en PubMed coincide con el configurado.
  - Al menos un autor está afiliado a un centro cuyo nombre contiene
    la palabra clave (insensible a mayúsculas/acentos).
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
DELAY_BETWEEN_REQUESTS = 0.4  # segundos (límite NCBI: ~3 req/s sin API key)
DEFAULT_CONFIG = "filter_config.json"


def normalize(text: str) -> str:
    """Minúsculas, sin acentos, sin espacios extra."""
    nfkd = unicodedata.normalize("NFKD", text.strip().lower())
    return " ".join(nfkd.encode("ascii", "ignore").decode("ascii").split())


def load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        sys.exit(f"Error: no se encuentra el archivo de configuración '{config_path}'")
    with open(path, encoding="utf-8") as fh:
        config = json.load(fh)
    for key in ("year", "affiliation_keyword"):
        if key not in config:
            sys.exit(f"Error: falta la clave '{key}' en la configuración.")
    return config


def fetch_pubmed(pmid: str) -> dict | None:
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
        el = article.find(xpath)
        return el.text.strip() if el is not None and el.text else ""

    # Año de publicación
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

    # Título
    title = text(".//ArticleTitle")

    # Autores con sus afiliaciones
    authors = []
    for a in article.findall(".//AuthorList/Author"):
        last = a.findtext("LastName", "").strip()
        initials = a.findtext("Initials", "").strip()
        name = f"{last} {initials}".strip() if last else a.findtext("CollectiveName", "").strip()
        affiliations = [
            aff.text.strip()
            for aff in a.findall(".//AffiliationInfo/Affiliation")
            if aff.text
        ]
        if name:
            authors.append({"name": name, "affiliations": affiliations})

    return {
        "title": title,
        "doi": doi,
        "year": year,
        "authors": authors,
    }


def matching_authors(authors: list, keyword_normalized: str) -> list[dict]:
    """Devuelve autor y afiliación literal para los autores que coinciden con la palabra clave."""
    matched = []
    for author in authors:
        for aff in author["affiliations"]:
            if keyword_normalized in normalize(aff):
                matched.append({"name": author["name"], "affiliation": aff})
                break
    return matched


def main(csv_path: str, config_path: str):
    config = load_config(config_path)
    target_year = config["year"].strip()
    keyword_normalized = normalize(config["affiliation_keyword"])

    path = Path(csv_path)
    if not path.exists():
        sys.exit(f"Error: no se encuentra '{csv_path}'")

    with open(path, newline="", encoding="utf-8-sig") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        all_rows = list(csv.reader(fh, dialect))

    if len(all_rows) < 4:
        sys.exit("El CSV no tiene suficientes filas (se esperan al menos 4).")

    headers = [h.strip() for h in all_rows[1]]
    data_rows = all_rows[3:]

    print(f"Archivo : {path.name}  |  Filas de datos: {len(data_rows)}")
    print(f"Filtros : año={target_year}  |  centro contiene='{config['affiliation_keyword']}'")
    print("=" * 70)

    results = []

    for i, raw in enumerate(data_rows, start=4):
        if not any(c.strip() for c in raw):
            continue

        row = dict(zip(headers, raw))
        pmid = row.get("PMID (PubMed Identifier)", "").strip()

        if not pmid:
            continue

        pubmed = fetch_pubmed(pmid)
        time.sleep(DELAY_BETWEEN_REQUESTS)

        if pubmed is None:
            print(f"  [OMITIDO] PMID {pmid} no encontrado en PubMed", file=sys.stderr)
            continue

        if pubmed["year"] != target_year:
            continue

        matched = matching_authors(pubmed["authors"], keyword_normalized)
        if not matched:
            continue

        results.append({
            "titulo": pubmed["title"],
            "doi": pubmed["doi"] or "—",
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "matched": matched,
        })

    # Salida
    print(f"\nArticulos que cumplen los filtros: {len(results)}\n")
    for n, r in enumerate(results, start=1):
        print(f"{n}. {r['titulo']}")
        print(f"   DOI    : {r['doi']}")
        print(f"   URL    : {r['url']}")
        for m in r["matched"]:
            print(f"   Autor  : {m['name']}")
            print(f"   Centro : {m['affiliation']}")
        print()


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        sys.exit("Uso: python filter_pubmed.py <archivo.csv> [config.json]")
    config_file = sys.argv[2] if len(sys.argv) == 3 else DEFAULT_CONFIG
    main(sys.argv[1], config_file)

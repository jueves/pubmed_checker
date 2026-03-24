#!/usr/bin/env python3
"""
Valida los datos de un CSV de papers contra la API de PubMed.
Uso: python check_pubmed.py <archivo.csv>

Estructura del CSV:
  Fila 1 (índice 0): ignorada (puede ser título del documento, etc.)
  Fila 2 (índice 1): headers
  Fila 3 (índice 2): explicaciones (ignorada)
  Fila 4+           : datos
"""

import csv
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
DELAY_BETWEEN_REQUESTS = 0.4  # segundos (límite NCBI: ~3 req/s sin API key)


# ---------------------------------------------------------------------------
# Helpers de normalización
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    """Minúsculas, sin acentos, sin puntuación extra."""
    if not text:
        return ""
    text = text.strip().lower()
    # Normalizar caracteres unicode (eliminar acentos)
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_str.split())


def similar(a: str, b: str, threshold: float = 0.6) -> bool:
    """Comprobación difusa: True si la similitud es >= threshold."""
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return False
    if a == b:
        return True
    # Palabras en común / total
    words_a = set(a.split())
    words_b = set(b.split())
    if not words_a or not words_b:
        return False
    common = words_a & words_b
    score = len(common) / max(len(words_a), len(words_b))
    return score >= threshold


# ---------------------------------------------------------------------------
# Llamada a la API de PubMed
# ---------------------------------------------------------------------------

def fetch_pubmed(pmid: str) -> dict | None:
    """
    Obtiene metadatos de un artículo por PMID.
    Devuelve un dict con las claves:
        title, first_author, authors, journal, year, volume,
        first_page, last_page, doi, is_oa
    o None si no se encuentra.
    """
    params = {
        "db": "pubmed",
        "id": pmid.strip(),
        "retmode": "xml",
        "rettype": "abstract",
    }
    try:
        resp = requests.get(EFETCH_URL, params=params, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  [ERROR HTTP] PMID {pmid}: {exc}")
        return None

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as exc:
        print(f"  [ERROR XML] PMID {pmid}: {exc}")
        return None

    article = root.find(".//PubmedArticle")
    if article is None:
        return None

    def text(xpath):
        el = article.find(xpath)
        return el.text.strip() if el is not None and el.text else ""

    # Título
    title = text(".//ArticleTitle")

    # Autores
    author_nodes = article.findall(".//AuthorList/Author")
    authors = []
    for a in author_nodes:
        last = a.findtext("LastName", "").strip()
        fore = a.findtext("ForeName", "").strip()
        initials = a.findtext("Initials", "").strip()
        if last:
            display = f"{last} {initials}" if initials else last
            authors.append(display)
        else:
            # Autor colectivo
            collective = a.findtext("CollectiveName", "").strip()
            if collective:
                authors.append(collective)
    first_author = authors[0] if authors else ""

    # Revista
    journal = (
        text(".//Journal/Title")
        or text(".//MedlineJournalInfo/MedlineTA")
    )

    # Año de publicación (PubDate > Year tiene prioridad)
    year = (
        text(".//Journal/JournalIssue/PubDate/Year")
        or text(".//Journal/JournalIssue/PubDate/MedlineDate")[:4]
    )

    # Volumen
    volume = text(".//Journal/JournalIssue/Volume")

    # Páginas
    medline_pgn = text(".//MedlinePgn")
    first_page, last_page = "", ""
    if medline_pgn:
        parts = medline_pgn.split("-")
        first_page = parts[0].strip()
        if len(parts) > 1:
            # PubMed a veces abrevia: "123-8" → "123-128"
            end = parts[1].strip()
            if len(end) < len(first_page):
                end = first_page[: len(first_page) - len(end)] + end
            last_page = end

    # DOI
    doi = ""
    for id_node in article.findall(".//ArticleIdList/ArticleId"):
        if id_node.get("IdType") == "doi":
            doi = id_node.text.strip() if id_node.text else ""
            break

    # Open Access (campo PublicationStatus o PubMedPubDate)
    # PubMed no devuelve directamente OA; usamos PMC como indicador proxy
    pmc = ""
    for id_node in article.findall(".//ArticleIdList/ArticleId"):
        if id_node.get("IdType") == "pmc":
            pmc = id_node.text.strip() if id_node.text else ""
            break

    return {
        "title": title,
        "first_author": first_author,
        "authors": authors,
        "journal": journal,
        "year": year,
        "volume": volume,
        "first_page": first_page,
        "last_page": last_page,
        "doi": doi,
        "pmc": pmc,
    }


# ---------------------------------------------------------------------------
# Comparación de campos
# ---------------------------------------------------------------------------

FIELD_CHECKS = [
    # (campo CSV, campo PubMed, etiqueta, comparador)
    ("Título",        "title",        "Título",         lambda a, b: similar(a, b, 0.5)),
    ("Primer Autor",  "first_author", "Primer Autor",   lambda a, b: similar(a, b, 0.5)),
    ("Revista",       "journal",      "Revista",        lambda a, b: similar(a, b, 0.4)),
    ("Año",           "year",         "Año",            lambda a, b: normalize(a) == normalize(b)),
    ("Volumen",       "volume",       "Volumen",        lambda a, b: normalize(a) == normalize(b)),
    ("Primera Página","first_page",   "Primera Página", lambda a, b: normalize(a) == normalize(b)),
    ("Ultima Página", "last_page",    "Última Página",  lambda a, b: normalize(a) == normalize(b)),
]


def check_row(row_num: int, row: dict, pubmed: dict) -> list[str]:
    """Devuelve lista de discrepancias encontradas."""
    issues = []
    for csv_field, pm_field, label, cmp in FIELD_CHECKS:
        csv_val = row.get(csv_field, "").strip()
        pm_val = pubmed.get(pm_field, "").strip()
        if not csv_val and not pm_val:
            continue  # ambos vacíos → ok
        if csv_val and not pm_val:
            issues.append(f"  {label}: CSV='{csv_val}' | PubMed=<vacío>")
        elif not csv_val and pm_val:
            issues.append(f"  {label}: CSV=<vacío> | PubMed='{pm_val}'")
        elif not cmp(csv_val, pm_val):
            issues.append(f"  {label}: CSV='{csv_val}' | PubMed='{pm_val}'")
    return issues


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(csv_path: str):
    path = Path(csv_path)
    if not path.exists():
        print(f"Error: no se encuentra el archivo '{csv_path}'")
        sys.exit(1)

    results = {
        "ok": 0,
        "discrepancias": 0,
        "sin_pmid": 0,
        "no_encontrado": 0,
        "errores": 0,
    }

    with open(path, newline="", encoding="utf-8-sig") as fh:
        # Detectar delimitador
        sample = fh.read(4096)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel  # fallback a coma

        reader = csv.reader(fh, dialect)
        all_rows = list(reader)

    if len(all_rows) < 4:
        print("El CSV no tiene suficientes filas (se esperan al menos 4).")
        sys.exit(1)

    # Fila índice 1 → headers
    headers = [h.strip() for h in all_rows[1]]
    # Filas de datos: índice 3 en adelante
    data_rows = all_rows[3:]

    print(f"Archivo: {path.name}")
    print(f"Columnas detectadas: {headers}")
    print(f"Filas de datos: {len(data_rows)}")
    print("=" * 70)

    for i, raw_row in enumerate(data_rows, start=4):  # nº de fila real en CSV
        # Saltar filas completamente vacías
        if not any(cell.strip() for cell in raw_row):
            continue

        row = dict(zip(headers, raw_row))
        pmid = row.get("PMID (PubMed Identifier)", "").strip()

        print(f"\nFila {i} | PMID: {pmid or '—'}")
        # Mostrar título del CSV para contexto
        titulo_csv = row.get("Título", "").strip()
        if titulo_csv:
            print(f"  CSV Título: {titulo_csv[:80]}{'...' if len(titulo_csv) > 80 else ''}")

        if not pmid:
            print("  [AVISO] Sin PMID — no se puede verificar")
            results["sin_pmid"] += 1
            continue

        pubmed = fetch_pubmed(pmid)
        time.sleep(DELAY_BETWEEN_REQUESTS)

        if pubmed is None:
            print(f"  [ERROR] PMID {pmid} no encontrado en PubMed")
            results["no_encontrado"] += 1
            continue

        issues = check_row(i, row, pubmed)

        if issues:
            print(f"  [DISCREPANCIAS]")
            for issue in issues:
                print(issue)
            results["discrepancias"] += 1
        else:
            print("  [OK] Todos los campos verificados coinciden")
            results["ok"] += 1

    print("\n" + "=" * 70)
    print("RESUMEN")
    print(f"  Correctos          : {results['ok']}")
    print(f"  Con discrepancias  : {results['discrepancias']}")
    print(f"  Sin PMID           : {results['sin_pmid']}")
    print(f"  No encontrados     : {results['no_encontrado']}")
    print(f"  Errores            : {results['errores']}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python check_pubmed.py <archivo.csv>")
        sys.exit(1)
    main(sys.argv[1])

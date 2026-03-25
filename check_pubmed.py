#!/usr/bin/env python3
"""
Valida los datos de un CSV/XLS/XLSX de papers contra la API de PubMed.
Uso: python check_pubmed.py <archivo.csv|xlsx|xls> [--sheet <nombre_hoja>]

Estructura del archivo:
  Fila 1: ignorada
  Fila 2: headers
  Fila 3: explicaciones (ignorada)
  Fila 4+: datos
"""

import argparse
import csv
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
DELAY_BETWEEN_REQUESTS = 0.4  # segundos (límite NCBI: ~3 req/s sin API key)

# Campos a comparar: (columna CSV, clave PubMed, etiqueta)
FIELDS = [
    ("Título",         "title",        "Título"),
    ("Primer Autor",   "first_author", "Primer Autor"),
    ("Revista",        "journal",      "Revista"),
    ("Año",            "year",         "Año"),
    ("Volumen",        "volume",       "Volumen"),
    ("Primera Página", "first_page",   "Primera Página"),
    ("Ultima Página",  "last_page",    "Última Página"),
]


def _cell_to_str(value) -> str:
    """Convierte un valor de celda Excel a string, evitando notación float para enteros."""
    if value is None:
        return ""
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value).strip()


def read_rows(path: Path, sheet_name: str | None = None) -> list[list[str]]:
    """Lee filas de un CSV, XLSX o XLS y las devuelve como listas de strings."""
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm", ".xltx", ".xltm"):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb[sheet_name] if sheet_name else wb.active
        rows = [[_cell_to_str(cell) for cell in row] for row in ws.iter_rows(values_only=True)]
        wb.close()
        return rows
    elif suffix == ".xls":
        import xlrd
        wb = xlrd.open_workbook(str(path))
        ws = wb.sheet_by_name(sheet_name) if sheet_name else wb.sheet_by_index(0)
        return [
            [_cell_to_str(ws.cell_value(r, c)) for c in range(ws.ncols)]
            for r in range(ws.nrows)
        ]
    else:
        if sheet_name:
            sys.exit("Error: --sheet solo es válido para archivos Excel (.xlsx, .xls).")
        with open(path, newline="", encoding="utf-8-sig") as fh:
            sample = fh.read(4096)
            fh.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            except csv.Error:
                dialect = csv.excel
            return list(csv.reader(fh, dialect))


def normalize(text: str) -> str:
    """Minúsculas, sin acentos, sin espacios extra."""
    nfkd = unicodedata.normalize("NFKD", text.strip().lower())
    return " ".join(nfkd.encode("ascii", "ignore").decode("ascii").split())


def fetch_pubmed(pmid: str) -> dict | None:
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
        el = article.find(xpath)
        return el.text.strip() if el is not None and el.text else ""

    # Autores
    authors = []
    for a in article.findall(".//AuthorList/Author"):
        last = a.findtext("LastName", "").strip()
        initials = a.findtext("Initials", "").strip()
        if last:
            authors.append(f"{last} {initials}".strip())
        elif collective := a.findtext("CollectiveName", "").strip():
            authors.append(collective)

    # Páginas (PubMed abrevia: "123-8" → expandir a "123-128")
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


def main(csv_path: str, sheet_name: str | None = None):
    path = Path(csv_path)
    if not path.exists():
        sys.exit(f"Error: no se encuentra '{csv_path}'")

    all_rows = read_rows(path, sheet_name)

    if len(all_rows) < 4:
        sys.exit("El CSV no tiene suficientes filas (se esperan al menos 4).")

    headers  = [h.strip() for h in all_rows[1]]
    data_rows = all_rows[3:]

    required_columns = [csv_col for csv_col, _, _ in FIELDS] + ["PMID (PubMed Identifier)"]
    missing = [col for col in required_columns if col not in headers]
    if missing:
        sys.exit(
            "Error: el CSV no contiene las siguientes columnas requeridas:\n"
            + "\n".join(f"  - {col}" for col in missing)
        )

    totals = {"ok": 0, "diferencias": 0, "sin_pmid": 0, "no_encontrado": 0}

    print(f"Archivo : {path.name}  |  Filas de datos: {len(data_rows)}")
    print("=" * 70)

    for i, raw in enumerate(data_rows, start=4):
        if not any(c.strip() for c in raw):
            continue

        row  = dict(zip(headers, raw))
        pmid = row.get("PMID (PubMed Identifier)", "").strip()

        print(f"\nFila {i} | PMID: {pmid or '—'}")

        if not pmid:
            print("  Sin PMID — omitido")
            totals["sin_pmid"] += 1
            continue

        pubmed = fetch_pubmed(pmid)
        time.sleep(DELAY_BETWEEN_REQUESTS)

        if pubmed is None:
            print(f"  PMID {pmid} no encontrado en PubMed")
            totals["no_encontrado"] += 1
            continue

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
            totals["diferencias"] += 1
        else:
            print("  OK")
            totals["ok"] += 1

    print("\n" + "=" * 70)
    print("RESUMEN")
    print(f"  Sin diferencias  : {totals['ok']}")
    print(f"  Con diferencias  : {totals['diferencias']}")
    print(f"  Sin PMID         : {totals['sin_pmid']}")
    print(f"  No encontrados   : {totals['no_encontrado']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Valida datos bibliográficos contra PubMed."
    )
    parser.add_argument("archivo", help="CSV, XLSX o XLS con los datos")
    parser.add_argument("--sheet", metavar="HOJA", default=None,
                        help="Nombre de la hoja a leer (solo para Excel; por defecto la primera)")
    args = parser.parse_args()
    main(args.archivo, args.sheet)

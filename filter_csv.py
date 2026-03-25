#!/usr/bin/env python3
"""
Filtra artículos desde un CSV usando PubMed solo para obtener la afiliación.

Flujo:
  1. Lee el CSV y filtra por año (sin llamadas a la API).
  2. Para cada fila que pasa el filtro de año, consulta PubMed por PMID
     para obtener las afiliaciones reales de los autores.
  3. Conserva solo los artículos en los que al menos un autor esté afiliado
     a un centro cuyo nombre contiene la palabra clave.

Salida: título, autores del centro, nombre del centro, año,
        open access, factor de impacto, cuartil y URL de PubMed.

Uso:
  python filter_csv.py <archivo.csv> --year AÑO --keyword PALABRA
  python filter_csv.py <archivo.csv> [config.json]
  python filter_csv.py <archivo.csv> [config.json] --author
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
DELAY_BETWEEN_REQUESTS = 0.4
DEFAULT_CONFIG = "filter_config.json"


def normalize(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text.strip().lower())
    return " ".join(nfkd.encode("ascii", "ignore").decode("ascii").split())


def load_csv(path: Path) -> tuple[list[str], list[dict]]:
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
    data_rows = [
        dict(zip(headers, [c.strip() for c in row]))
        for row in all_rows[3:]
        if any(c.strip() for c in row)
    ]
    return headers, data_rows


def parse_args():
    parser = argparse.ArgumentParser(
        description="Filtra artículos de un CSV por año y afiliación (vía PubMed)."
    )
    parser.add_argument("csv", help="Ruta al archivo CSV")
    parser.add_argument("config", nargs="?", help="Archivo de configuración JSON (opcional)")
    parser.add_argument("--year", help="Año de publicación")
    parser.add_argument("--keyword", default="", help="Palabra clave del centro/hospital")
    parser.add_argument("--debug", action="store_true", help="Muestra columnas y valores únicos detectados")
    parser.add_argument("--author", action="store_true", help="Agrupa los resultados por autor")

    args = parser.parse_args()

    if args.year:
        return args.csv, args.year.strip(), args.keyword.strip(), args.debug, args.author

    config_path = args.config or DEFAULT_CONFIG
    cfg_file = Path(config_path)
    if not cfg_file.exists():
        sys.exit(f"Error: faltan --year o no se encuentra el config '{config_path}'.")
    with open(cfg_file, encoding="utf-8") as fh:
        config = json.load(fh)
    if "year" not in config:
        sys.exit("Error: falta la clave 'year' en la configuración.")
    return args.csv, config["year"].strip(), config.get("affiliation_keyword", "").strip(), args.debug, args.author


def fetch_affiliations(pmid: str) -> list[dict] | None:
    """Devuelve lista de {name, affiliations} para el PMID dado, o None si falla."""
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
        initials = a.findtext("Initials", "").strip()
        name = f"{last} {initials}".strip() if last else a.findtext("CollectiveName", "").strip()
        affiliations = [
            aff.text.strip()
            for aff in a.findall(".//AffiliationInfo/Affiliation")
            if aff.text
        ]
        if name:
            authors.append({"name": name, "affiliations": affiliations})
    return authors


def matching_authors(authors: list, keyword_norm: str) -> list[dict]:
    matched = []
    for author in authors:
        for aff in author["affiliations"]:
            if keyword_norm in normalize(aff):
                matched.append({"name": author["name"], "affiliation": aff})
                break
    return matched


def main():
    csv_path_str, target_year, keyword, debug, author_mode = parse_args()

    path = Path(csv_path_str)
    if not path.exists():
        sys.exit(f"Error: no se encuentra '{csv_path_str}'")

    headers, rows = load_csv(path)
    keyword_norm = normalize(keyword)

    filtro_centro = f"  |  centro contiene='{keyword}'" if keyword else ""
    print(f"Archivo : {path.name}  |  Filas de datos: {len(rows)}")
    print(f"Filtros : año={target_year}{filtro_centro}")
    print("=" * 70)

    if debug:
        print("\n[DEBUG] Columnas detectadas:")
        for i, h in enumerate(headers):
            print(f"  {i:2d}: {repr(h)}")
        años = sorted({r.get("Año", "").strip() for r in rows} - {""})
        print(f"\n[DEBUG] Valores únicos en 'Año': {años}")
        print()

    # Paso 1: filtrar por año desde el CSV (sin API)
    year_filtered = [r for r in rows if r.get("Año", "").strip() == target_year]
    print(f"Filas con año {target_year}: {len(year_filtered)}")
    if keyword:
        print(f"Consultando PubMed para {len(year_filtered)} artículo(s)...\n")

    results = []
    for row in year_filtered:
        pmid = row.get("PMID (PubMed Identifier)", "").strip()

        # Si hay keyword, verificar afiliación en PubMed
        if keyword:
            if not pmid:
                print(f"  [OMITIDO] Sin PMID: {row.get('Título', '')[:60]}", file=sys.stderr)
                continue
            authors = fetch_affiliations(pmid)
            time.sleep(DELAY_BETWEEN_REQUESTS)
            if authors is None:
                print(f"  [OMITIDO] PMID {pmid} no encontrado en PubMed", file=sys.stderr)
                continue
            matched = matching_authors(authors, keyword_norm)
            if not matched:
                continue
            autores_list = [m["name"] for m in matched]
            autores_str = "; ".join(autores_list)
            centro_str = matched[0]["affiliation"]
        else:
            # Sin keyword: mostrar autores del CSV
            autores_str = _format_authors(row)
            autores_list = _parse_authors(row)
            centro_str = row.get("Servicio al que pertenece en el HUGCDN", "—").strip() or "—"

        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "—"
        results.append({
            "titulo":       row.get("Título", "—").strip() or "—",
            "autores":      autores_str or "—",
            "autores_list": autores_list,
            "centro":       centro_str or "—",
            "año":          row.get("Año", "—").strip() or "—",
            "oa":           row.get("Open Access", "—").strip() or "—",
            "if":           row.get("Impact Factor 2024", "—").strip() or "—",
            "cuartil":      row.get("Cuartil", "—").strip() or "—",
            "url":          url,
        })

    print(f"\nArtículos que cumplen los filtros: {len(results)}\n")

    if author_mode:
        _print_by_author(results)
    else:
        for n, r in enumerate(results, start=1):
            print(f"{n}. {r['titulo']}")
            print(f"   Autores centro : {r['autores']}")
            print(f"   Centro      : {r['centro']}")
            print(f"   Año         : {r['año']}")
            print(f"   Open Access : {r['oa']}")
            print(f"   IF          : {r['if']}")
            print(f"   Cuartil     : {r['cuartil']}")
            print(f"   PubMed URL  : {r['url']}")
            print()


def _format_authors(row: dict) -> str:
    parts = []
    first = row.get("Primer Autor", "").strip()
    rest = row.get("Resto de Autores", "").strip()
    if first:
        parts.append(first)
    if rest:
        parts.append(rest)
    return "; ".join(parts) if parts else "—"


def _parse_authors(row: dict) -> list[str]:
    """Devuelve lista de nombres de autor a partir de las columnas del CSV."""
    authors = []
    first = row.get("Primer Autor", "").strip()
    rest = row.get("Resto de Autores", "").strip()
    if first:
        authors.append(first)
    if rest:
        authors.extend([a.strip() for a in rest.split(";") if a.strip()])
    return authors


def _print_by_author(results: list[dict]) -> None:
    """Imprime los artículos agrupados por autor."""
    # Construir índice: autor -> lista de artículos
    author_index: dict[str, list[dict]] = {}
    for r in results:
        for author in r["autores_list"]:
            author_index.setdefault(author, []).append(r)

    print(f"Autores con publicaciones: {len(author_index)}\n")
    print("=" * 70)
    for author in sorted(author_index):
        articulos = author_index[author]
        print(f"\nAUTOR: {author}  ({len(articulos)} artículo(s))")
        print("-" * 60)
        for n, r in enumerate(articulos, start=1):
            print(f"  {n}. {r['titulo']}")
            print(f"     IF: {r['if']}  |  Cuartil: {r['cuartil']}  |  Open Access: {r['oa']}")
            print(f"     URL: {r['url']}")
        print()


if __name__ == "__main__":
    main()

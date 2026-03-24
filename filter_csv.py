#!/usr/bin/env python3
"""
Filtra artículos directamente desde un CSV (sin consultar PubMed).

Uso:
  python filter_csv.py <archivo.csv> --year AÑO --keyword PALABRA
  python filter_csv.py <archivo.csv> [config.json]

Criterios de filtrado:
  - El año de publicación (columna 'Año') coincide con el indicado.
  - El servicio/centro (columna 'Servicio al que pertenece en el HUGCDN')
    contiene la palabra clave (insensible a mayúsculas/acentos).

Salida por artículo:
  Título, autores del centro, nombre del centro, año,
  open access, factor de impacto y cuartil.
"""

import argparse
import csv
import json
import sys
import unicodedata
from pathlib import Path

DEFAULT_CONFIG = "filter_config.json"


def normalize(text: str) -> str:
    """Minúsculas, sin acentos, sin espacios extra."""
    nfkd = unicodedata.normalize("NFKD", text.strip().lower())
    return " ".join(nfkd.encode("ascii", "ignore").decode("ascii").split())


def load_csv(path: Path) -> tuple[list[str], list[dict]]:
    """Devuelve (headers, filas_como_dict). Asume estructura de 3 cabeceras."""
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


def parse_args() -> tuple[str, str, str]:
    """Devuelve (csv_path, year, keyword)."""
    parser = argparse.ArgumentParser(
        description="Filtra artículos de un CSV por año y centro."
    )
    parser.add_argument("csv", help="Ruta al archivo CSV")
    parser.add_argument("config", nargs="?", help="Archivo de configuración JSON (opcional)")
    parser.add_argument("--year", help="Año de publicación")
    parser.add_argument("--keyword", help="Palabra clave del centro/servicio")

    args = parser.parse_args()

    # Si se dan --year y --keyword directamente, úsalos
    if args.year and args.keyword:
        return args.csv, args.year.strip(), args.keyword.strip()

    # Si no, busca config JSON
    config_path = args.config or DEFAULT_CONFIG
    cfg_file = Path(config_path)
    if not cfg_file.exists():
        sys.exit(
            f"Error: faltan --year/--keyword o no se encuentra el config '{config_path}'."
        )
    with open(cfg_file, encoding="utf-8") as fh:
        config = json.load(fh)
    for key in ("year", "affiliation_keyword"):
        if key not in config:
            sys.exit(f"Error: falta la clave '{key}' en la configuración.")
    return args.csv, config["year"].strip(), config["affiliation_keyword"].strip()


def format_authors(row: dict) -> str:
    """Combina primer autor y resto de autores en una sola cadena."""
    parts = []
    first = row.get("Primer Autor", "").strip()
    rest = row.get("Resto de Autores", "").strip()
    if first:
        parts.append(first)
    if rest:
        parts.append(rest)
    return "; ".join(parts) if parts else "—"


def main():
    csv_path_str, target_year, keyword = parse_args()

    path = Path(csv_path_str)
    if not path.exists():
        sys.exit(f"Error: no se encuentra '{csv_path_str}'")

    _, rows = load_csv(path)
    keyword_norm = normalize(keyword)

    print(f"Archivo : {path.name}  |  Filas de datos: {len(rows)}")
    print(f"Filtros : año={target_year}  |  centro contiene='{keyword}'")
    print("=" * 70)

    results = []
    for row in rows:
        year_csv = row.get("Año", "").strip()
        centro = row.get("Servicio al que pertenece en el HUGCDN", "").strip()

        if year_csv != target_year:
            continue
        if keyword_norm and keyword_norm not in normalize(centro):
            continue

        results.append({
            "titulo":    row.get("Título", "—").strip() or "—",
            "autores":   format_authors(row),
            "centro":    centro or "—",
            "año":       year_csv or "—",
            "oa":        row.get("Open Access", "—").strip() or "—",
            "if":        row.get("Impact Factor 2024", "—").strip() or "—",
            "cuartil":   row.get("Cuartil", "—").strip() or "—",
        })

    print(f"\nArtículos que cumplen los filtros: {len(results)}\n")
    for n, r in enumerate(results, start=1):
        print(f"{n}. {r['titulo']}")
        print(f"   Autores     : {r['autores']}")
        print(f"   Centro      : {r['centro']}")
        print(f"   Año         : {r['año']}")
        print(f"   Open Access : {r['oa']}")
        print(f"   IF          : {r['if']}")
        print(f"   Cuartil     : {r['cuartil']}")
        print()


if __name__ == "__main__":
    main()

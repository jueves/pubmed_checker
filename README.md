# pubmed_checker

Scripts Python para validar y filtrar publicaciones de un CSV contra la API de PubMed (NCBI).

## Scripts disponibles

| Script | Función |
|--------|---------|
| `check_pubmed.py` | Valida los datos bibliográficos del CSV contra PubMed |
| `filter_csv.py` | Filtra artículos por año y afiliación (modo principal) |
| `filter_pubmed.py` | Filtra artículos verificando el año directamente en PubMed |

---

## Estructura del CSV

Todos los scripts esperan el mismo formato de CSV:

- **Fila 1:** título o metadato libre (ignorada)
- **Fila 2:** cabeceras de columna
- **Fila 3:** descripciones de cada columna (ignorada)
- **Fila 4+:** datos de publicaciones

---

## check_pubmed.py

Valida los datos bibliográficos de cada fila contra los datos oficiales de PubMed.

### Uso

```bash
python check_pubmed.py <archivo.csv>
```

Para cada fila con PMID, el script consulta PubMed y compara los campos del CSV con los datos oficiales. La comparación normaliza el texto: minúsculas, sin acentos y sin espacios extra.

### Variables que SÍ se verifican

| Columna CSV       | Campo PubMed         |
|-------------------|----------------------|
| `Título`          | `ArticleTitle`       |
| `Primer Autor`    | Primer elemento de `AuthorList` |
| `Revista`         | `Journal/Title`      |
| `Año`             | `PubDate/Year`       |
| `Volumen`         | `JournalIssue/Volume`|
| `Primera Página`  | Primera parte de `MedlinePgn` |
| `Ultima Página`   | Segunda parte de `MedlinePgn` (expandida si está abreviada) |

### Variables que NO se verifican

| Columna CSV                              | Motivo                                              |
|------------------------------------------|-----------------------------------------------------|
| `Resto de Autores`                       | Solo se comprueba el primer autor                   |
| `Servicio al que pertenece en el HUGCDN` | Dato institucional interno, no disponible en PubMed |
| `Colaboración con otros grupos de Investigación` | Dato interno                              |
| `Ámbito`                                 | Dato interno (Nacional/Internacional)               |
| `Impact Factor 2024`                     | No disponible en la API de PubMed                   |
| `Cuartil`                                | No disponible en la API de PubMed                   |
| `Tipo Publicación`                       | No se verifica actualmente                          |
| `Open Access`                            | No verificable de forma fiable mediante la API de PubMed (ver nota) |

> **Nota sobre Open Access:** La API de PubMed permite detectar si un artículo está disponible en PubMed Central (PMC) mediante la presencia de un PMC ID, lo que correspondería a los artículos marcados como "Free PMC Article" en la web. Sin embargo, muchos artículos de libre acceso están alojados únicamente en la web del publicador y no tienen PMC ID, por lo que esta señal no cubre todos los casos posibles. Al no poder verificar el acceso libre de forma exhaustiva, el campo `Open Access` no se contrasta.

---

## filter_csv.py

Filtra artículos del CSV por año de publicación y, opcionalmente, por afiliación de los autores consultando PubMed.

### Uso

```bash
# Con argumentos directos
python filter_csv.py <archivo.csv> --year AÑO [--keyword PALABRA] [--author] [--debug]

# Con archivo de configuración JSON
python filter_csv.py <archivo.csv> [config.json]
python filter_csv.py <archivo.csv> [config.json] --author
```

### Opciones

| Opción | Descripción |
|--------|-------------|
| `--year AÑO` | Año de publicación a filtrar (requerido si no hay config) |
| `--keyword PALABRA` | Palabra clave para filtrar por afiliación del autor en PubMed |
| `--author` | Agrupa los resultados por autor en lugar de listarlos por artículo |
| `--debug` | Muestra las columnas detectadas y los años disponibles en el CSV |

### Archivo de configuración (JSON)

Si no se indica `--year`, el script busca un archivo `filter_config.json` (o el que se especifique):

```json
{
  "year": "2024",
  "affiliation_keyword": "Hospital"
}
```

### Flujo de filtrado

1. Lee el CSV y filtra por año **sin llamadas a la API**.
2. Si se indica `--keyword`, consulta PubMed por PMID para obtener las afiliaciones reales de los autores.
3. Conserva solo los artículos en los que al menos un autor esté afiliado a un centro cuyo nombre contiene la palabra clave.

### Salida por artículo

```
1. Título del artículo
   Autores centro : Apellido Nombre
   Centro         : Hospital Universitario ...
   Año            : 2024
   Open Access    : Sí
   FI             : 5.2
   Cuartil        : Q1
   PubMed URL     : https://pubmed.ncbi.nlm.nih.gov/PMID/
```

### Salida por autor (`--author`)

```
AUTOR: Apellido Nombre  (2 artículo(s))
------------------------------------------------------------
  1. Título del artículo
     FI: 5.2  |  Cuartil: Q1  |  Open Access: Sí
     URL: https://pubmed.ncbi.nlm.nih.gov/PMID/
```

---

## filter_pubmed.py

Similar a `filter_csv.py`, pero verifica el año de publicación directamente en PubMed (más estricto: ignora el año del CSV). Requiere siempre `affiliation_keyword`.

### Uso

```bash
python filter_pubmed.py <archivo.csv> [config.json]
```

El archivo de configuración (por defecto `filter_config.json`) debe contener:

```json
{
  "year": "2024",
  "affiliation_keyword": "Hospital"
}
```

La salida incluye título, DOI, URL de PubMed y los autores con su afiliación completa.

---

## Requisitos

```bash
pip install requests
```

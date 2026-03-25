# pubmed_checker

Script Python que valida los datos bibliográficos de un CSV/Excel de publicaciones contra la API de PubMed (NCBI).

## Uso

```bash
# CSV
python check_pubmed.py <archivo.csv>

# Excel (primera hoja)
python check_pubmed.py <archivo.xlsx>

# Excel (hoja específica)
python check_pubmed.py <archivo.xlsx> --sheet "Publicaciones"
```

El archivo debe seguir esta estructura:
- **Fila 1:** título o metadato libre (ignorada)
- **Fila 2:** cabeceras de columna
- **Fila 3:** descripciones de cada columna (ignorada)
- **Fila 4+:** datos de publicaciones

Para cada fila con PMID, el script consulta PubMed y compara los campos del CSV con los datos oficiales.

---

## Variables que SÍ se verifican

Estos campos del CSV se comparan contra los datos devueltos por PubMed:

| Columna CSV       | Campo PubMed         |
|-------------------|----------------------|
| `Título`          | `ArticleTitle`       |
| `Primer Autor`    | Primer elemento de `AuthorList` |
| `Revista`         | `Journal/Title`      |
| `Año`             | `PubDate/Year`       |
| `Volumen`         | `JournalIssue/Volume`|
| `Primera Página`  | Primera parte de `MedlinePgn` |
| `Ultima Página`   | Segunda parte de `MedlinePgn` (expandida si está abreviada) |

La comparación se hace normalizando el texto: minúsculas, sin acentos y sin espacios extra.

---

## Variables que NO se verifican

Estas columnas del CSV se leen pero **no se contrastan con PubMed**:

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

## Requisitos

```bash
pip install requests openpyxl   # para CSV y XLSX
pip install xlrd                # adicionalmente para XLS (formato antiguo)
```

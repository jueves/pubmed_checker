# pubmed_checker

Script Python que valida los datos bibliográficos de un CSV de publicaciones contra la API de PubMed (NCBI).

## Uso

```bash
python check_pubmed.py <archivo.csv>
```

El CSV debe seguir esta estructura:
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
| `Open Access`                            | No disponible en la API de PubMed                   |

---

## Requisitos

```bash
pip install requests
```

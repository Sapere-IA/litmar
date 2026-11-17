# litmar

`litmar` es una librería de Python para reunir literatura científica de varias
fuentes en una sola biblioteca. Busca en servicios con API pública, convierte
cada resultado a un modelo común (`Articulo`), elimina duplicados por DOI y
exporta el conjunto a BibTeX para usarlo en LaTeX o en un gestor de referencias.

## Instalación

Requiere Python 3.12 o superior y [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

## Uso

```python
from litmar import Biblioteca
from litmar.fuentes import OpenAlex, Pangaea

biblioteca = Biblioteca()
with OpenAlex() as openalex, Pangaea() as pangaea:
    for articulo in openalex.buscar("ocean acidification", n=20):
        biblioteca.agregar(articulo)
    for dataset in pangaea.buscar("ocean acidification", n=20):
        biblioteca.agregar(dataset)

print(len(biblioteca))
biblioteca.exportar_bibtex("referencias.bib")
```

- **`Articulo`**: registro inmutable con título, autores, año, DOI, revista,
  resumen y número de citas. El DOI se normaliza (sin `https://doi.org/`, en
  minúsculas) y actúa como identidad.
- **`Biblioteca`**: colección ordenada sin duplicados. `agregar` devuelve `False`
  si el DOI ya estaba; `obtener(doi)` admite el DOI en cualquier formato.
- **Fuentes**: todas heredan de `Fuente` e implementan
  `buscar(consulta, n) -> list[Articulo]`. Reintentan solas ante errores de red,
  `429` y `5xx`, con espera exponencial y respetando `Retry-After`.

## Fuentes

### OpenAlex

Catálogo abierto de trabajos académicos. La clave de API es opcional: sin ella se
comparte un presupuesto diario por IP; con una gratuita
(<https://openalex.org/settings/api>) el presupuesto es propio y mayor.

```python
OpenAlex(api_key="...")
```

Se descartan los trabajos sin DOI o sin autores.

### PANGAEA

[PANGAEA](https://www.pangaea.de/) es un repositorio de **datasets** de ciencias
de la Tierra y del medio ambiente. No necesita clave.

- La consulta se busca en su índice público de Elasticsearch y deben aparecer
  todos los términos. Se acepta cualquier texto, incluidas comillas, barras o
  paréntesis.
- Como un dataset no tiene revista, el campo `journal` es siempre `"PANGAEA"`.
- `citations` es siempre `0`: PANGAEA no publica ese dato.
- Los autores llegan como `"Apellido, Nombre"`. Las instituciones conservan su
  nombre completo.
- Se descartan los datasets a los que les falta DOI, título, año o autores.
- Elasticsearch no deja pasar del resultado 10 000, así que `buscar` nunca
  devuelve resultados más allá de esa posición.

Al exportar a BibTeX, los datasets aparecen como entradas `@article`, igual que
los artículos.

## Tests

```bash
uv run pytest
```

Los tests no usan la red: las fuentes se prueban con `httpx.MockTransport` y
respuestas reales guardadas en `tests/datos/`.

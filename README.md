# litmar

Herramientas para buscar artículos científicos y guardarlos en una biblioteca
identificada por DOI.

## Fuentes

Las fuentes disponibles se importan desde `litmar.fuentes`:

```python
from litmar.fuentes import Arxiv, OpenAlex

with Arxiv() as arxiv:
    articulos = arxiv.buscar("quantum computing", n=10)

with OpenAlex() as openalex:
    articulos_openalex = openalex.buscar("quantum computing", n=10)
```

`buscar(consulta, n=10)` devuelve como máximo `n` objetos `Articulo`. Como el
modelo de la biblioteca requiere un DOI, la fuente arXiv omite resultados que
no incluyan uno. arXiv no proporciona número de citas en esta API; esos
artículos usan el valor predeterminado `0`.

La fuente arXiv consulta la [API oficial](https://info.arxiv.org/help/api/user-manual.html)
y espera al menos tres segundos entre peticiones de una misma instancia, de
acuerdo con sus [términos de uso](https://info.arxiv.org/help/api/tou.html).
Las aplicaciones que usen varias instancias o procesos deben coordinar sus
llamadas para respetar el límite global. La API devuelve XML Atom; los errores
HTTP y los errores de consulta comunicados por arXiv se propagan al llamador.

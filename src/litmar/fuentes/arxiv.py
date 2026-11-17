"""Fuente arXiv: https://info.arxiv.org/help/api/user-manual.html"""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree

from litmar.articulo import Articulo
from litmar.fuentes.base import Fuente

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"


class Arxiv(Fuente):
    """Búsqueda en arXiv. Solo devuelve entradas que incluyen un DOI."""

    BASE_URL = "https://export.arxiv.org/api/query"
    MAX_PER_PAGE = 2_000
    PAGING_LIMIT = 30_000
    MIN_REQUEST_INTERVAL = 3.0

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("min_request_interval", self.MIN_REQUEST_INTERVAL)
        super().__init__(**kwargs)

    def buscar(self, consulta: str, n: int = 10) -> list[Articulo]:
        if n < 1:
            raise ValueError("n debe ser al menos 1")
        if not consulta.strip():
            raise ValueError("la consulta no puede estar vacía")

        per_page = min(n, self.MAX_PER_PAGE)
        articulos: list[Articulo] = []
        inicio = 0
        while len(articulos) < n and inicio < self.PAGING_LIMIT:
            max_results = min(per_page, self.PAGING_LIMIT - inicio)
            respuesta = self._get_response(
                self.BASE_URL,
                params={
                    "search_query": f"all:{consulta}",
                    "start": inicio,
                    "max_results": max_results,
                },
            )
            feed = ElementTree.fromstring(respuesta.content)
            entradas = feed.findall(f"{ATOM}entry")
            error = next(
                (entrada for entrada in entradas if _texto(entrada.findtext(f"{ATOM}title")) == "Error"),
                None,
            )
            if error is not None:
                raise ValueError(
                    f"Error en la consulta de arXiv: {_texto(error.findtext(f'{ATOM}summary'))}"
                )
            for entrada in entradas:
                articulo = a_articulo(entrada)
                if articulo is not None:
                    articulos.append(articulo)
                    if len(articulos) == n:
                        break
            if len(articulos) == n or len(entradas) < max_results:
                break
            inicio += max_results
        return articulos


def a_articulo(entrada: ElementTree.Element) -> Articulo | None:
    """Convierte una entrada Atom; descarta las que no encajan con Articulo."""
    titulo = _texto(entrada.findtext(f"{ATOM}title"))
    autores = [
        _texto(autor.findtext(f"{ATOM}name"))
        for autor in entrada.findall(f"{ATOM}author")
    ]
    autores = [autor for autor in autores if autor]
    publicado = _texto(entrada.findtext(f"{ATOM}published"))
    doi = _texto(entrada.findtext(f"{ARXIV}doi"))
    if not doi:
        doi = next(
            (
                enlace.get("href", "")
                for enlace in entrada.findall(f"{ATOM}link")
                if enlace.get("title") == "doi"
            ),
            "",
        )
    if not (titulo and autores and publicado and doi):
        return None
    try:
        anio = int(publicado[:4])
        return Articulo(
            title=titulo,
            authors=autores,
            year=anio,
            doi=doi,
            journal=_texto(entrada.findtext(f"{ARXIV}journal_ref")),
            abstract=_texto(entrada.findtext(f"{ATOM}summary")),
        )
    except ValueError:
        return None


def _texto(valor: str | None) -> str:
    return " ".join((valor or "").split())

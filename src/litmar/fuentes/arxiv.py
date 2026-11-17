"""Fuente arXiv: https://info.arxiv.org/help/api/user-manual.html"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from typing import Any

from litmar.articulo import Articulo
from litmar.fuentes.base import Fuente

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}
_PREFIJO_ID = re.compile(r"^https?://arxiv\.org/abs/")
_VERSION = re.compile(r"v\d+$")


class ErrorArXiv(Exception):
    """arXiv informa de los errores con HTTP 200 y una entrada titulada «Error»."""


class ArXiv(Fuente):
    """Búsqueda de preprints en arXiv.

    No necesita clave. Los términos de uso piden como mucho una petición cada
    3 segundos, así que cada intento (reintentos incluidos) espera lo que
    falte desde el anterior.

    arXiv solo da DOI cuando el autor lo indica; en otro caso se usa el DOI
    que DataCite asigna a todo artículo de arXiv: 10.48550/arXiv.<id>.
    """

    BASE_URL = "https://export.arxiv.org/api/query"
    MAX_PER_PAGE = 2000
    PAGING_LIMIT = 30_000  # start + max_results no puede superarlo
    PAUSA = 3.0  # segundos mínimos entre peticiones

    def __init__(self, *, reloj: Callable[[], float] = time.monotonic, **kwargs: Any) -> None:
        kwargs.setdefault("backoff", self.PAUSA)
        super().__init__(**kwargs)
        self._reloj = reloj
        self._ultima_peticion: float | None = None

    def buscar(self, consulta: str, n: int = 10) -> list[Articulo]:
        if n < 1:
            raise ValueError("n debe ser al menos 1")
        per_page = min(n, self.MAX_PER_PAGE)
        articulos: list[Articulo] = []
        inicio = 0
        while len(articulos) < n and inicio + per_page <= self.PAGING_LIMIT:
            respuesta = self._get(
                self.BASE_URL,
                params={
                    "search_query": construir_consulta(consulta),
                    "start": inicio,
                    "max_results": per_page,
                    "sortBy": "relevance",
                    "sortOrder": "descending",
                },
            )
            entradas = leer_entradas(respuesta.content)
            for entrada in entradas:
                articulo = a_articulo(entrada)
                if articulo is not None:
                    articulos.append(articulo)
                    if len(articulos) == n:
                        break
            if len(entradas) < per_page:  # no hay más páginas
                break
            inicio += per_page
        return articulos

    def _antes_de_peticion(self) -> None:
        if self._ultima_peticion is not None:
            espera = self.PAUSA - (self._reloj() - self._ultima_peticion)
            if espera > 0:
                self._sleep(espera)
        self._ultima_peticion = self._reloj()


def construir_consulta(consulta: str) -> str:
    """«deep learning» → «all:deep AND all:learning».

    Si la consulta ya usa la sintaxis de arXiv (ti:, au:, cat:...), se envía tal cual.
    """
    if ":" in consulta:
        return consulta
    return " AND ".join(f"all:{palabra}" for palabra in consulta.split())


def leer_entradas(xml: bytes) -> list[ET.Element]:
    """Devuelve las <entry> del feed Atom; lanza ErrorArXiv si arXiv informa de un error."""
    entradas = ET.fromstring(xml).findall("atom:entry", NS)
    for entrada in entradas:
        if "/api/errors" in _texto(entrada, "atom:id"):
            raise ErrorArXiv(_texto(entrada, "atom:summary") or "error desconocido de arXiv")
    return entradas


def a_articulo(entrada: ET.Element) -> Articulo | None:
    """Convierte una <entry> de arXiv en Articulo; None si le faltan datos obligatorios."""
    autores = [nombre for autor in entrada.findall("atom:author", NS) if (nombre := _texto(autor, "atom:name"))]
    arxiv_id = _VERSION.sub("", _PREFIJO_ID.sub("", _texto(entrada, "atom:id")))
    titulo, publicado = _texto(entrada, "atom:title"), _texto(entrada, "atom:published")
    if not (arxiv_id and titulo and publicado and autores):
        return None
    try:
        return Articulo(
            title=titulo,
            authors=autores,
            year=int(publicado[:4]),
            doi=_texto(entrada, "arxiv:doi") or doi_datacite(arxiv_id),
            journal=_texto(entrada, "arxiv:journal_ref") or "arXiv",
            abstract=_texto(entrada, "atom:summary"),
        )
    except ValueError:  # p. ej. fecha o DOI con formato inesperado
        return None


def doi_datacite(arxiv_id: str) -> str:
    """DOI que DataCite asigna a cada artículo de arXiv (sin versión)."""
    return f"10.48550/arXiv.{arxiv_id}"


def _texto(elemento: ET.Element, ruta: str) -> str:
    """Texto del hijo indicado con los espacios normalizados (arXiv parte los títulos en líneas)."""
    hijo = elemento.find(ruta, NS)
    return " ".join((hijo.text or "").split()) if hijo is not None else ""

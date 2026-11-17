"""Fuente Zotero (API web v3): https://www.zotero.org/support/dev/web_api/v3/basics"""

from __future__ import annotations

import re
from typing import Any, Literal

from litmar.articulo import Articulo
from litmar.fuentes.base import Fuente

_DOI_EN_EXTRA = re.compile(r"^\s*DOI:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_ANIO = re.compile(r"\b(\d{4})\b")


class Zotero(Fuente):
    """Búsqueda de artículos en una biblioteca de Zotero (de usuario o de grupo).

    A diferencia de OpenAlex, no busca en toda la literatura científica sino en
    los elementos guardados en *una* biblioteca. Hace falta su identificador:

    - Biblioteca personal: el «userID» que aparece en
      https://www.zotero.org/settings/keys (no es el nombre de usuario).
    - Grupo: el número de la URL https://www.zotero.org/groups/<id>/...

    La clave es obligatoria para bibliotecas privadas y opcional para las
    públicas. Se crea en https://www.zotero.org/settings/keys y se envía en la
    cabecera Zotero-API-Key para que no aparezca en URLs ni logs.

    Zotero no tiene recuento de citas, así que `citations` siempre vale 0.
    """

    BASE_URL = "https://api.zotero.org"
    API_VERSION = "3"
    MAX_PER_PAGE = 100  # límite de la API para `limit`

    def __init__(
        self,
        library_id: str | int,
        library_type: Literal["user", "group"] = "user",
        api_key: str | None = None,
        **kwargs: Any,
    ) -> None:
        if library_type not in ("user", "group"):
            raise ValueError("library_type debe ser 'user' o 'group'")
        if not str(library_id).isdigit():
            raise ValueError(f"library_id debe ser numérico: {library_id!r}")
        super().__init__(**kwargs)
        self.library_id = str(library_id)
        self.library_type = library_type
        self.api_key = api_key

    @property
    def url_items(self) -> str:
        # /items/top excluye adjuntos (PDF) y notas hijas de otros elementos.
        return f"{self.BASE_URL}/{self.library_type}s/{self.library_id}/items/top"

    def buscar(self, consulta: str, n: int = 10) -> list[Articulo]:
        if n < 1:
            raise ValueError("n debe ser al menos 1")
        headers = {"Zotero-API-Version": self.API_VERSION}
        if self.api_key:
            headers["Zotero-API-Key"] = self.api_key
        limite = min(n, self.MAX_PER_PAGE)
        articulos: list[Articulo] = []
        inicio = 0
        # Se descartan elementos sin DOI, sin autores o sin año: puede hacer falta más de una página.
        while len(articulos) < n:
            resultados = self._get_json(
                self.url_items,
                params={"q": consulta, "format": "json", "limit": limite, "start": inicio},
                headers=headers,
            ) or []
            for elemento in resultados:
                articulo = a_articulo(elemento)
                if articulo is not None:
                    articulos.append(articulo)
                    if len(articulos) == n:
                        break
            if len(resultados) < limite:  # no hay más páginas
                break
            inicio += limite
        return articulos


def a_articulo(elemento: dict[str, Any]) -> Articulo | None:
    """Convierte un elemento de Zotero en Articulo; None si le faltan datos obligatorios."""
    datos = elemento.get("data") or {}
    autores = [
        nombre
        for creador in datos.get("creators") or []
        if creador.get("creatorType") == "author" and (nombre := nombre_creador(creador))
    ]
    doi = extraer_doi(datos)
    titulo = (datos.get("title") or "").strip()
    anio = extraer_anio(elemento)
    if not (doi and titulo and anio and autores):
        return None
    try:
        return Articulo(
            title=titulo,
            authors=autores,
            year=anio,
            doi=doi,
            journal=datos.get("publicationTitle") or "",
            abstract=(datos.get("abstractNote") or "").strip(),
        )
    except ValueError:  # p. ej. DOI con formato inesperado
        return None


def nombre_creador(creador: dict[str, Any]) -> str:
    """Zotero guarda el nombre partido (lastName/firstName) o entero (name, p. ej. instituciones).

    Se devuelve «Apellido, Nombre», el formato que entiende `apellido()`.
    """
    if nombre := (creador.get("name") or "").strip():
        return nombre
    apellido = (creador.get("lastName") or "").strip()
    nombre = (creador.get("firstName") or "").strip()
    if apellido and nombre:
        return f"{apellido}, {nombre}"
    return apellido or nombre


def extraer_doi(datos: dict[str, Any]) -> str | None:
    """El campo DOI solo existe en algunos tipos (p. ej. journalArticle).

    En el resto, Zotero recomienda anotarlo en «Extra» como una línea «DOI: 10.xxx/yyy».
    """
    if doi := (datos.get("DOI") or "").strip():
        return doi
    if coincidencia := _DOI_EN_EXTRA.search(datos.get("extra") or ""):
        return coincidencia.group(1)
    return None


def extraer_anio(elemento: dict[str, Any]) -> int | None:
    """El campo `date` es texto libre («March 2016», «2016-03-01»...).

    Zotero ya lo interpreta en meta.parsedDate («2016-03-01», «2016-03» o «2016»);
    si no está, se busca un año de 4 cifras en el texto original.
    """
    for texto in ((elemento.get("meta") or {}).get("parsedDate"), (elemento.get("data") or {}).get("date")):
        if texto and (coincidencia := _ANIO.search(texto)):
            return int(coincidencia.group(1))
    return None

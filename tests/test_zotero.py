"""Tests de la fuente Zotero. Ninguno usa la red: httpx.MockTransport responde en su lugar."""

import json
from pathlib import Path

import httpx
import pytest

from litmar.fuentes import Zotero
from litmar.fuentes.zotero import extraer_anio, extraer_doi, nombre_creador

# Respuesta de ejemplo con el formato de GET /users/<id>/items/top?format=json
# (escrita a mano según la documentación de la API v3). De sus 4 elementos solo
# 2 son válidos: un libro sin DOI y un capítulo sin autores deben descartarse.
RESPUESTA_ZOTERO = json.loads(
    (Path(__file__).parent / "datos" / "zotero_items.json").read_text(encoding="utf-8")
)


def cliente(*respuestas, peticiones=None):
    """Cliente httpx que devuelve las respuestas en orden y anota cada petición."""
    pendientes = list(respuestas)

    def manejar(peticion: httpx.Request) -> httpx.Response:
        if peticiones is not None:
            peticiones.append(peticion)
        r = pendientes.pop(0)
        return r if isinstance(r, httpx.Response) else httpx.Response(200, json=r)

    return httpx.Client(transport=httpx.MockTransport(manejar))


def test_zotero_convierte_respuesta():
    articulos = Zotero(1234567, client=cliente(RESPUESTA_ZOTERO, [])).buscar("citation", n=4)

    assert [a.doi for a in articulos] == ["10.1016/j.jbusres.2016.11.009", "10.5555/who.2020.001"]
    primero = articulos[0]
    assert primero.title.startswith("Mapping the luxury research landscape")
    assert primero.authors == ("Gurzki, Hannes", "Woisetschläger, David M.")
    assert primero.year == 2017
    assert primero.journal == "Journal of Business Research"
    assert primero.abstract == "Luxury research has grown considerably."
    assert primero.citations == 0
    assert primero.short_citation == "Gurzki y Woisetschläger (2017)"
    # Autor institucional y DOI anotado en «Extra».
    assert articulos[1].authors == ("World Health Organization",)


def test_zotero_parametros_de_la_peticion():
    peticiones = []
    Zotero(1234567, client=cliente(RESPUESTA_ZOTERO, [], peticiones=peticiones)).buscar("deep learning", n=4)

    peticion = peticiones[0]
    assert peticion.url.host == "api.zotero.org"
    assert peticion.url.path == "/users/1234567/items/top"
    params = peticion.url.params
    assert params["q"] == "deep learning"
    assert params["format"] == "json"
    assert params["limit"] == "4"
    assert params["start"] == "0"
    assert peticion.headers["zotero-api-version"] == "3"
    assert "zotero-api-key" not in peticion.headers


def test_zotero_biblioteca_de_grupo():
    peticiones = []
    Zotero("98765", library_type="group", client=cliente([], peticiones=peticiones)).buscar("x")
    assert peticiones[0].url.path == "/groups/98765/items/top"


def test_zotero_envia_la_clave_en_cabecera_y_no_en_la_url():
    peticiones = []
    Zotero(1, api_key="secreta", client=cliente(RESPUESTA_ZOTERO, [], peticiones=peticiones)).buscar("x", n=4)
    assert peticiones[0].headers["zotero-api-key"] == "secreta"
    assert "secreta" not in str(peticiones[0].url)


def test_zotero_pide_otra_pagina_si_descarta_elementos():
    # Con n=3 la primera página trae 3 elementos, pero solo 2 son válidos.
    peticiones = []
    articulos = Zotero(1, client=cliente(RESPUESTA_ZOTERO[:3], [], peticiones=peticiones)).buscar("x", n=3)
    assert len(articulos) == 2
    assert [p.url.params["start"] for p in peticiones] == ["0", "3"]


def test_zotero_no_devuelve_mas_de_n():
    assert len(Zotero(1, client=cliente(RESPUESTA_ZOTERO)).buscar("x", n=1)) == 1


def test_zotero_hereda_los_reintentos():
    esperas = []
    fuente = Zotero(1, client=cliente(httpx.Response(429, headers={"Retry-After": "5"}), RESPUESTA_ZOTERO, []), sleep=esperas.append)
    assert len(fuente.buscar("x", n=4)) == 2
    assert esperas == [5.0]


@pytest.mark.parametrize(
    "kwargs",
    [{"library_id": "abc"}, {"library_id": 1, "library_type": "team"}],
)
def test_zotero_parametros_invalidos(kwargs):
    with pytest.raises(ValueError):
        Zotero(**kwargs)


def test_zotero_n_invalido():
    with pytest.raises(ValueError):
        Zotero(1, client=cliente()).buscar("x", n=0)


def test_nombre_creador():
    assert nombre_creador({"firstName": "Ana", "lastName": "García López"}) == "García López, Ana"
    assert nombre_creador({"name": "CERN"}) == "CERN"
    assert nombre_creador({"lastName": "Platón", "firstName": ""}) == "Platón"


def test_extraer_doi():
    assert extraer_doi({"DOI": " 10.1/abc "}) == "10.1/abc"
    assert extraer_doi({"extra": "PMID: 123\ndoi: 10.2/xyz"}) == "10.2/xyz"
    assert extraer_doi({"extra": "sin doi"}) is None


def test_extraer_anio():
    assert extraer_anio({"meta": {"parsedDate": "2016-03-01"}}) == 2016
    assert extraer_anio({"meta": {}, "data": {"date": "Spring 2019"}}) == 2019
    assert extraer_anio({"data": {"date": ""}}) is None

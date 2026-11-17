"""Tests de Fuente y OpenAlex. Ninguno usa la red: httpx.MockTransport responde en su lugar."""

import json
from pathlib import Path

import httpx
import pytest

from litmar.fuentes import Arxiv, Fuente, OpenAlex
from litmar.fuentes.openalex import reconstruir_resumen

# Respuesta real de
# GET /works?search=bibliometrics citation analysis&per_page=3&select=...
# guardada el 2026-11-17. Contiene una obra sin DOI, que debe descartarse.
RESPUESTA_OPENALEX = json.loads(
    (Path(__file__).parent / "datos" / "openalex_works.json").read_text(encoding="utf-8")
)
VACIA = {"meta": {}, "results": []}


def cliente(*respuestas, peticiones=None):
    """Cliente httpx que devuelve las respuestas en orden y anota cada petición.

    Cada respuesta es un httpx.Response, un dict (JSON con 200) o una excepción.
    """
    pendientes = list(respuestas)

    def manejar(peticion: httpx.Request) -> httpx.Response:
        if peticiones is not None:
            peticiones.append(peticion)
        r = pendientes.pop(0)
        if isinstance(r, Exception):
            raise r
        return r if isinstance(r, httpx.Response) else httpx.Response(200, json=r)

    return httpx.Client(transport=httpx.MockTransport(manejar))


class FuenteDePrueba(Fuente):
    """Implementación mínima para probar los reintentos de la clase base."""

    def buscar(self, consulta, n=10):
        return self._get_json("https://ejemplo.org/api", params={"q": consulta})


def fuente_de_prueba(*respuestas, **kwargs):
    esperas = []
    fuente = FuenteDePrueba(client=cliente(*respuestas), sleep=esperas.append, **kwargs)
    return fuente, esperas


# --- Fuente: clase base -------------------------------------------------------


def test_fuente_es_abstracta():
    with pytest.raises(TypeError):
        Fuente()


def test_reintenta_429_respetando_retry_after():
    fuente, esperas = fuente_de_prueba(
        httpx.Response(429, headers={"Retry-After": "2"}), {"ok": True}
    )
    assert fuente.buscar("x") == {"ok": True}
    assert esperas == [2.0]


def test_reintenta_5xx_con_espera_exponencial():
    fuente, esperas = fuente_de_prueba(
        httpx.Response(503), httpx.Response(500), httpx.Response(502), {"ok": True}, backoff=0.5
    )
    assert fuente.buscar("x") == {"ok": True}
    assert esperas == [0.5, 1.0, 2.0]


def test_reintenta_errores_de_red():
    fuente, esperas = fuente_de_prueba(httpx.ConnectTimeout("lento"), {"ok": True})
    assert fuente.buscar("x") == {"ok": True}
    assert esperas == [1.0]


def test_agota_reintentos_y_lanza_error():
    fuente, esperas = fuente_de_prueba(*[httpx.Response(503)] * 3, max_retries=2)
    with pytest.raises(httpx.HTTPStatusError):
        fuente.buscar("x")
    assert len(esperas) == 2  # 3 intentos, 2 esperas


def test_agota_reintentos_de_red_y_lanza_error():
    fuente, _ = fuente_de_prueba(*[httpx.ConnectError("caído")] * 2, max_retries=1)
    with pytest.raises(httpx.ConnectError):
        fuente.buscar("x")


@pytest.mark.parametrize("estado", [400, 401, 403, 404])
def test_no_reintenta_errores_del_cliente(estado):
    fuente, esperas = fuente_de_prueba(httpx.Response(estado))
    with pytest.raises(httpx.HTTPStatusError):
        fuente.buscar("x")
    assert esperas == []


def test_no_espera_si_retry_after_supera_max_wait():
    # Caso típico: presupuesto diario agotado y el servidor pide esperar horas.
    fuente, esperas = fuente_de_prueba(httpx.Response(429, headers={"Retry-After": "3600"}))
    with pytest.raises(httpx.HTTPStatusError):
        fuente.buscar("x")
    assert esperas == []


def test_retry_after_como_fecha_pasada_no_espera():
    fuente, esperas = fuente_de_prueba(
        httpx.Response(503, headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}), {"ok": True}
    )
    assert fuente.buscar("x") == {"ok": True}
    assert esperas == [0.0]


# --- OpenAlex -----------------------------------------------------------------


def test_openalex_convierte_respuesta_guardada():
    articulos = OpenAlex(client=cliente(RESPUESTA_OPENALEX)).buscar("bibliometrics", n=2)

    assert [a.doi for a in articulos] == ["10.1515/libr.1996.46.3.149", "10.1016/j.jbusres.2016.11.009"]
    primero = articulos[0]
    assert primero.title.startswith("Bibliometrics, Citation Analysis and Co-Citation Analysis")
    assert primero.authors == ("Farideh Osareh",)
    assert primero.year == 1996
    assert primero.journal == "Libri"
    assert primero.citations == 702
    assert primero.abstract == ""
    assert articulos[1].short_citation == "Gurzki y Woisetschläger (2016)"


def test_openalex_parametros_de_la_peticion():
    peticiones = []
    OpenAlex(client=cliente(RESPUESTA_OPENALEX, peticiones=peticiones)).buscar("deep learning", n=2)

    (peticion,) = peticiones
    assert peticion.url.host == "api.openalex.org"
    assert peticion.url.path == "/works"
    params = peticion.url.params
    assert params["search"] == "deep learning"
    assert params["per_page"] == "2"
    assert params["page"] == "1"
    assert "abstract_inverted_index" in params["select"]
    assert "authorization" not in peticion.headers


def test_openalex_envia_la_clave_en_cabecera_y_no_en_la_url():
    peticiones = []
    OpenAlex(api_key="secreta", client=cliente(RESPUESTA_OPENALEX, peticiones=peticiones)).buscar("x", n=2)
    assert peticiones[0].headers["authorization"] == "Bearer secreta"
    assert "secreta" not in str(peticiones[0].url)


def test_openalex_pide_otra_pagina_si_descarta_obras():
    # Con n=3 la página trae 3 obras, pero una no tiene DOI: hay que pedir la página 2.
    peticiones = []
    articulos = OpenAlex(client=cliente(RESPUESTA_OPENALEX, VACIA, peticiones=peticiones)).buscar("x", n=3)
    assert len(articulos) == 2
    assert [p.url.params["page"] for p in peticiones] == ["1", "2"]


def test_openalex_no_devuelve_mas_de_n():
    assert len(OpenAlex(client=cliente(RESPUESTA_OPENALEX)).buscar("x", n=1)) == 1


def test_openalex_n_invalido():
    with pytest.raises(ValueError):
        OpenAlex(client=cliente()).buscar("x", n=0)


def test_openalex_hereda_los_reintentos():
    esperas = []
    fuente = OpenAlex(client=cliente(httpx.Response(429), RESPUESTA_OPENALEX), sleep=esperas.append)
    assert len(fuente.buscar("x", n=2)) == 2
    assert esperas == [1.0]


def test_reconstruir_resumen():
    indice = {"in": [3, 5], "Despite": [0], "growing": [1], "interest": [2], "AI": [4], "ethics": [6]}
    assert reconstruir_resumen(indice) == "Despite growing interest in AI in ethics"
    assert reconstruir_resumen(None) == ""


# --- arXiv --------------------------------------------------------------------


def respuesta_arxiv(*entradas):
    return httpx.Response(
        200,
        text=(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:arxiv="http://arxiv.org/schemas/atom">'
            + "".join(entradas)
            + "</feed>"
        ),
    )


def entrada_arxiv(doi=True, title="A paper", arxiv_id="2301.00001"):
    campo_doi = "<arxiv:doi>10.1234/example</arxiv:doi>" if doi else ""
    return (
        "<entry>"
        f"<id>https://arxiv.org/abs/{arxiv_id}</id>"
        f"<title>{title}</title>"
        "<published>2023-01-02T03:04:05Z</published>"
        "<summary>Paper abstract.</summary>"
        "<author><name>Jane Doe</name></author>"
        f"{campo_doi}"
        "<arxiv:journal_ref>Journal of Tests</arxiv:journal_ref>"
        "</entry>"
    )


def test_arxiv_convierte_entrada_atom_a_articulo():
    articulos = Arxiv(client=cliente(respuesta_arxiv(entrada_arxiv()))).buscar("quantum")

    (articulo,) = articulos
    assert articulo.title == "A paper"
    assert articulo.authors == ("Jane Doe",)
    assert articulo.year == 2023
    assert articulo.doi == "10.1234/example"
    assert articulo.journal == "Journal of Tests"
    assert articulo.abstract == "Paper abstract."
    assert articulo.citations == 0


def test_arxiv_parametros_de_busqueda():
    peticiones = []
    Arxiv(client=cliente(respuesta_arxiv(entrada_arxiv()), peticiones=peticiones)).buscar(
        "deep learning", n=4
    )

    (peticion,) = peticiones
    assert peticion.url.host == "export.arxiv.org"
    assert peticion.url.path == "/api/query"
    assert peticion.url.params["search_query"] == "all:deep learning"
    assert peticion.url.params["start"] == "0"
    assert peticion.url.params["max_results"] == "4"


def test_arxiv_omite_entradas_sin_doi_y_respeta_el_intervalo():
    esperas = []
    peticiones = []
    fuente = Arxiv(
        client=cliente(
            respuesta_arxiv(entrada_arxiv(doi=False)),
            respuesta_arxiv(entrada_arxiv(arxiv_id="2301.00002")),
            peticiones=peticiones,
        ),
        sleep=esperas.append,
    )

    articulos = fuente.buscar("quantum", n=1)

    assert [articulo.doi for articulo in articulos] == ["10.1234/example"]
    assert [peticion.url.params["start"] for peticion in peticiones] == ["0", "1"]
    assert esperas == [3.0]


@pytest.mark.parametrize(("consulta", "n"), [("", 1), ("   ", 1), ("x", 0)])
def test_arxiv_rechaza_consulta_o_cantidad_invalida(consulta, n):
    with pytest.raises(ValueError):
        Arxiv(client=cliente()).buscar(consulta, n=n)


def test_arxiv_propaga_errores_de_la_api():
    feed_error = respuesta_arxiv(
        '<entry><id>https://arxiv.org/api/errors#bad_query</id>'
        "<title>Error</title><summary>bad query</summary></entry>"
    )
    with pytest.raises(ValueError, match="bad query"):
        Arxiv(client=cliente(feed_error)).buscar("quantum")

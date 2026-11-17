"""Clase base para fuentes de artículos, con reintentos HTTP."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import Lock
from typing import Any, Self

import httpx

from litmar.articulo import Articulo

# 429: límite de peticiones. 5xx: fallos transitorios del servidor.
ESTADOS_REINTENTABLES = frozenset({429, 500, 502, 503, 504})


class Fuente(ABC):
    """Fuente de artículos (OpenAlex, Crossref...).

    Las subclases implementan `buscar` y hacen sus peticiones con `_get_json`,
    que reintenta ante errores de red, 429 y 5xx con espera exponencial y
    respetando la cabecera Retry-After.
    """

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff: float = 1.0,
        max_wait: float = 60.0,
        min_request_interval: float = 0.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if min_request_interval < 0:
            raise ValueError("min_request_interval no puede ser negativo")
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=timeout)
        self.max_retries = max_retries
        self.backoff = backoff  # espera del primer reintento; se duplica en cada uno
        self.max_wait = max_wait  # si el servidor pide esperar más, se falla sin esperar
        self.min_request_interval = min_request_interval
        self._sleep = sleep
        self._request_lock = Lock()
        self._last_request_at: float | None = None

    @abstractmethod
    def buscar(self, consulta: str, n: int = 10) -> list[Articulo]:
        """Devuelve como mucho `n` artículos que coinciden con `consulta`."""

    def _get_json(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """GET con reintentos. Lanza httpx.HTTPStatusError o httpx.TransportError si se agotan."""
        return self._get_response(url, params, headers).json()

    def _get_response(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """GET con reintentos y el intervalo mínimo configurado entre peticiones."""
        for intento in range(self.max_retries + 1):
            ultimo = intento == self.max_retries
            try:
                with self._request_lock:
                    if self._last_request_at is not None:
                        transcurrido = time.monotonic() - self._last_request_at
                        espera = self.min_request_interval - transcurrido
                        if espera > 0:
                            self._sleep(espera)
                    self._last_request_at = time.monotonic()
                    respuesta = self.client.get(url, params=params, headers=headers)
            except httpx.TransportError:  # timeout, conexión rechazada, DNS...
                if ultimo:
                    raise
                espera = self._espera_exponencial(intento)
            else:
                if respuesta.status_code not in ESTADOS_REINTENTABLES or ultimo:
                    respuesta.raise_for_status()
                    return respuesta
                espera = self._espera_retry_after(respuesta)
                if espera is None:
                    espera = self._espera_exponencial(intento)
                if espera > self.max_wait:
                    # p. ej. presupuesto diario agotado: no tiene sentido esperar horas.
                    respuesta.raise_for_status()
            self._sleep(espera)
        raise AssertionError("inalcanzable")

    def _espera_exponencial(self, intento: int) -> float:
        return self.backoff * 2**intento

    @staticmethod
    def _espera_retry_after(respuesta: httpx.Response) -> float | None:
        """Interpreta Retry-After, que puede venir en segundos o como fecha HTTP."""
        valor = respuesta.headers.get("Retry-After")
        if not valor:
            return None
        try:
            return max(0.0, float(valor))
        except ValueError:
            pass
        try:
            fecha = parsedate_to_datetime(valor)
        except (TypeError, ValueError):
            return None
        return max(0.0, (fecha - datetime.now(timezone.utc)).total_seconds())

    def close(self) -> None:
        """Cierra el cliente HTTP si lo creó esta fuente."""
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

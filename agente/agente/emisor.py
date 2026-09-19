"""Envio de cruces a la API.

Responsable de la parte que mas falla en campo: la red. Tres garantias:

  1. Idempotencia. La clave se deriva del contenido del lote, no de un
     contador ni de un aleatorio. Si el agente se reinicia tras enviar pero
     antes de confirmar, el reenvio produce la misma clave y el servidor lo
     reconoce como duplicado en lugar de insertar los cruces dos veces.

  2. Reintento con espera creciente. Ante un fallo de red se espera cada vez
     mas, hasta un tope, para no saturar una red que ya esta en problemas.

  3. Renovacion de credencial. Un token expirado devuelve 401; el emisor se
     reautentica y reintenta en lugar de detenerse. Una jornada dura ocho
     horas y el agente debe sobrevivirla sin intervencion.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import UTC, datetime

import httpx

registro = logging.getLogger("agente.emisor")


class ErrorAutenticacion(RuntimeError):
    pass


class Emisor:
    def __init__(
        self,
        url_api: str,
        sala_id: str,
        hostname: str,
        email: str,
        password: str,
        tiempo_espera: float = 30.0,
        reintentos: int = 5,
    ):
        # `reintentos` se conserva por compatibilidad con el bucle interno, que
        # sigue disponible para uso directo. La composicion recomendada es
        # envolver este emisor con agente.canal.construir_canal, que saca la
        # politica de resiliencia fuera del emisor (patron Decorator).
        self.url_api = url_api.rstrip("/")
        self.sala_id = sala_id
        self.hostname = hostname
        self.email = email
        self.password = password
        self.reintentos = reintentos
        self.cliente = httpx.Client(timeout=tiempo_espera)
        self._token: str | None = None

    # --- autenticacion ----------------------------------------------------

    def autenticar(self) -> None:
        respuesta = self.cliente.post(
            f"{self.url_api}/v1/auth/token",
            json={"email": self.email, "password": self.password},
        )
        if respuesta.status_code != 200:
            raise ErrorAutenticacion(
                f"No se pudo autenticar ({respuesta.status_code}): {respuesta.text[:200]}"
            )
        self._token = respuesta.json()["access_token"]
        registro.info("Credencial obtenida para %s", self.email)

    def _cabeceras(self, clave_idempotencia: str) -> dict:
        if self._token is None:
            self.autenticar()
        return {
            "Authorization": f"Bearer {self._token}",
            "Idempotency-Key": clave_idempotencia,
        }

    # --- envio ------------------------------------------------------------

    @staticmethod
    def clave_idempotencia(sala_id: str, filas: list[dict]) -> str:
        """Clave determinista derivada del contenido del lote.

        Incluye los identificadores de la cola local, que son estables entre
        reinicios: dos ejecuciones del agente sobre las mismas filas producen
        la misma clave.
        """
        firma = "|".join(
            [
                sala_id,
                str(filas[0]["id"]),
                str(filas[-1]["id"]),
                str(len(filas)),
                filas[0]["ocurrido_en"],
                filas[-1]["ocurrido_en"],
            ]
        )
        return hashlib.sha256(firma.encode()).hexdigest()[:40]

    def enviar_lote(self, filas: list[dict]) -> dict | None:
        """Envia un lote. Devuelve la respuesta o None si no se logro enviar."""
        if not filas:
            return None

        cuerpo = {
            "sala_id": self.sala_id,
            "agente": self.hostname,
            "cruces": [
                {
                    "linea": fila["linea"],
                    "direccion": fila["direccion"],
                    "ocurrido_en": fila["ocurrido_en"],
                    "confianza": fila.get("confianza"),
                    "track_id_local": fila.get("track_id"),
                }
                for fila in filas
            ],
        }
        clave = self.clave_idempotencia(self.sala_id, filas)

        espera = 2.0
        for intento in range(1, self.reintentos + 1):
            try:
                respuesta = self.cliente.post(
                    f"{self.url_api}/v1/cruces:lote",
                    json=cuerpo,
                    headers=self._cabeceras(clave),
                )

                if respuesta.status_code == 401:
                    registro.warning("Credencial expirada, renovando")
                    self.autenticar()
                    continue

                if respuesta.status_code in (200, 201):
                    datos = respuesta.json()
                    if datos.get("duplicado"):
                        registro.info("Lote ya procesado por el servidor, se confirma local")
                    if datos.get("rechazados"):
                        registro.warning(
                            "El servidor rechazo %s cruces. Primer motivo: %s",
                            datos["rechazados"],
                            (datos.get("detalles") or [{}])[0].get("motivo", "sin detalle"),
                        )
                    return datos

                # 4xx distinto de 401 indica un problema de contenido: no se
                # corrige reintentando y hay que registrarlo para depuracion.
                if 400 <= respuesta.status_code < 500:
                    registro.error(
                        "Lote rechazado (%s): %s",
                        respuesta.status_code,
                        respuesta.text[:300],
                    )
                    return None

                registro.warning(
                    "Error del servidor (%s), intento %s de %s",
                    respuesta.status_code,
                    intento,
                    self.reintentos,
                )

            except (httpx.RequestError, ErrorAutenticacion) as exc:
                registro.warning(
                    "Fallo de red en el intento %s de %s: %s", intento, self.reintentos, exc
                )

            if intento < self.reintentos:
                time.sleep(espera)
                espera = min(espera * 2, 60.0)

        registro.error("No se pudo enviar el lote tras %s intentos; queda en cola", self.reintentos)
        return None

    # --- aforo y latido ---------------------------------------------------

    def enviar_aforo(self, conteo: int, momento: datetime | None = None) -> bool:
        """Reporta el conteo directo de ocupacion.

        Es el ancla que corrige la deriva del calculo acumulado: si la sala no
        tiene una entrada unica, N_in - N_out se desajusta a lo largo del dia.
        """
        momento = momento or datetime.now(UTC)
        try:
            respuesta = self.cliente.post(
                f"{self.url_api}/v1/aforos",
                json={
                    "sala_id": self.sala_id,
                    "ocurrido_en": momento.isoformat(),
                    "conteo": conteo,
                    "metodo": "VISION",
                },
                headers={"Authorization": f"Bearer {self._token or ''}"},
            )
            if respuesta.status_code == 401:
                self.autenticar()
                return False
            return respuesta.status_code in (200, 201)
        except httpx.RequestError as exc:
            registro.debug("No se pudo enviar el aforo: %s", exc)
            return False

    def cerrar(self) -> None:
        self.cliente.close()

"""Captura de video desde camara IP o archivo.

Una camara IP en la red de un hospital se desconecta: se reinicia el conmutador,
alguien desenchufa algo, el enlace inalambrico se cae. El agente debe
reconectarse solo, porque nadie va a estar vigilandolo durante la jornada.

Se lee en un hilo aparte y se conserva unicamente el ultimo cuadro. Es
deliberado: si la inferencia se atrasa, interesa procesar lo que esta pasando
ahora y no ir acumulando un retraso creciente sobre cuadros viejos. Para contar
cruces, saltarse cuadros intermedios es aceptable; medir con varios minutos de
desfase no lo es.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime

registro = logging.getLogger("agente.captura")


class CapturaRTSP:
    def __init__(
        self,
        url: str,
        fps_objetivo: float = 10.0,
        espera_reconexion: float = 5.0,
        ancho: int | None = None,
    ):
        self.url = url
        self.fps_objetivo = fps_objetivo
        self.espera_reconexion = espera_reconexion
        self.ancho = ancho

        self._cuadro = None
        self._momento: datetime | None = None
        self._cerrojo = threading.Lock()
        self._detener = threading.Event()
        self._hilo: threading.Thread | None = None
        self.conectada = False
        self.cuadros_leidos = 0
        self.reconexiones = 0

    def iniciar(self) -> None:
        self._hilo = threading.Thread(target=self._bucle, daemon=True, name="captura")
        self._hilo.start()

    def _abrir(self):
        import cv2

        captura = cv2.VideoCapture(self.url)
        # Buffer minimo: se quiere el cuadro mas reciente, no una cola de cuadros.
        try:
            captura.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:  # pragma: no cover - depende del backend de OpenCV
            pass
        return captura

    def _bucle(self) -> None:  # pragma: no cover - requiere fuente de video
        import cv2

        captura = None
        intervalo = 1.0 / self.fps_objetivo if self.fps_objetivo > 0 else 0.0
        ultimo = 0.0

        while not self._detener.is_set():
            if captura is None or not captura.isOpened():
                if captura is not None:
                    captura.release()
                    self.reconexiones += 1
                    registro.warning("Reconectando a %s", self._url_censurada())
                captura = self._abrir()
                if not captura.isOpened():
                    self.conectada = False
                    time.sleep(self.espera_reconexion)
                    continue
                self.conectada = True
                registro.info("Conectado a %s", self._url_censurada())

            leido, cuadro = captura.read()
            if not leido:
                self.conectada = False
                captura.release()
                captura = None
                time.sleep(self.espera_reconexion)
                continue

            ahora = time.monotonic()
            if intervalo and (ahora - ultimo) < intervalo:
                continue
            ultimo = ahora

            if self.ancho and cuadro.shape[1] > self.ancho:
                alto = int(cuadro.shape[0] * self.ancho / cuadro.shape[1])
                cuadro = cv2.resize(cuadro, (self.ancho, alto))

            with self._cerrojo:
                self._cuadro = cuadro
                self._momento = datetime.now(UTC)
                self.cuadros_leidos += 1

        if captura is not None:
            captura.release()

    def leer(self) -> tuple[object | None, datetime | None]:
        with self._cerrojo:
            return self._cuadro, self._momento

    def detener(self) -> None:
        self._detener.set()
        if self._hilo is not None:
            self._hilo.join(timeout=5)

    def _url_censurada(self) -> str:
        """Oculta la credencial de la camara antes de escribirla en el registro."""
        if "@" not in self.url:
            return self.url
        esquema, resto = self.url.split("://", 1)
        return f"{esquema}://***@{resto.split('@', 1)[1]}"


class CapturaSimulada:
    """Fuente sin video, para ejecutar el agente sin camara.

    Entrega marcas de tiempo a la cadencia configurada y ningun cuadro; el
    detector simulado no necesita imagen.
    """

    def __init__(self, fps_objetivo: float = 10.0, acelerar: float = 1.0):
        self.fps_objetivo = fps_objetivo
        self.acelerar = acelerar
        self.conectada = True
        self.cuadros_leidos = 0
        self.reconexiones = 0
        self._intervalo = 1.0 / fps_objetivo if fps_objetivo > 0 else 0.0
        self._proximo = 0.0

    def iniciar(self) -> None:
        self._proximo = time.monotonic()

    def leer(self) -> tuple[object | None, datetime | None]:
        espera = self._proximo - time.monotonic()
        if espera > 0:
            time.sleep(espera / max(self.acelerar, 0.001))
        self._proximo += self._intervalo
        self.cuadros_leidos += 1
        return None, datetime.now(UTC)

    def detener(self) -> None:
        self.conectada = False

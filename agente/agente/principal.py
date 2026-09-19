"""Bucle principal del agente de vision.

Orquesta los componentes: captura el video, detecta personas, cuenta cruces,
los encola localmente y los envia a la API en un hilo aparte.

El envio va en su propio hilo para que un problema de red no detenga la
medicion. Si el servidor no responde, la captura sigue y los cruces se
acumulan en la cola; cuando la red vuelve, se vacian solos.

PRIVACIDAD: en ningun punto se guarda un cuadro de video ni ningun rasgo de
las personas. El cuadro vive en memoria mientras se procesa y se descarta. Lo
unico que persiste es una lista de instantes en que alguien anonimo cruzo una
linea.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from agente import config as configuracion_modulo
from agente.canal import construir_canal
from agente.captura import CapturaRTSP, CapturaSimulada
from agente.cola import ColaLocal, antiguedad_minutos
from agente.conteo import ContadorSala
from agente.deteccion import construir_detector
from agente.emisor import Emisor

registro = logging.getLogger("agente")


class Agente:
    def __init__(self, cfg: configuracion_modulo.Configuracion):
        self.cfg = cfg
        self.detener = threading.Event()

        self.cola = ColaLocal(cfg.ruta_cola)
        self.contador = ContadorSala(cfg.construir_lineas())
        self.detector = construir_detector(cfg.detector)
        self.emisor = Emisor(
            url_api=cfg.url_api,
            sala_id=cfg.sala_id,
            hostname=cfg.hostname,
            email=cfg.email,
            password=cfg.password,
            # Un solo intento en el emisor: la politica de reintento la aporta
            # el canal decorado, de modo que se pueda cambiar sin tocar aqui.
            reintentos=1,
        )
        # Decorator: envio directo + reintentos + metricas.
        self.canal = construir_canal(self.emisor, intentos=5)

        if cfg.fuente == "rtsp":
            self.captura = CapturaRTSP(
                url=cfg.url_rtsp,
                fps_objetivo=cfg.fps_objetivo,
                ancho=cfg.ancho_proceso,
            )
        elif cfg.fuente == "archivo":
            self.captura = CapturaRTSP(
                url=cfg.url_rtsp,
                fps_objetivo=cfg.fps_objetivo,
                ancho=cfg.ancho_proceso,
            )
        else:
            self.captura = CapturaSimulada(fps_objetivo=cfg.fps_objetivo)

        self.cruces_contados = 0
        self._ultimo_aforo = 0.0
        self._ultimo_informe = 0.0

    # --- hilo de envio ----------------------------------------------------

    def _bucle_envio(self) -> None:
        while not self.detener.is_set():
            try:
                filas = self.cola.tomar_lote(self.cfg.tamano_lote)
                if filas:
                    resultado = self.canal.enviar(filas)
                    if resultado.exitoso:
                        self.cola.confirmar([fila["id"] for fila in filas])
                    else:
                        self.cola.registrar_intento_fallido([fila["id"] for fila in filas])
                        # Se espera antes de reintentar para no golpear una red
                        # que ya esta caida.
                        self.detener.wait(30)
                        continue
            except Exception:
                registro.exception("Error inesperado en el hilo de envio")

            self.detener.wait(self.cfg.intervalo_envio_s)

    # --- bucle principal --------------------------------------------------

    def ejecutar(self) -> None:
        registro.info(
            "Agente iniciado | sala=%s | hostname=%s | detector=%s | fuente=%s",
            self.cfg.sala_id,
            self.cfg.hostname,
            self.cfg.detector.get("tipo"),
            self.cfg.fuente,
        )
        registro.info(
            "Lineas configuradas: %s", ", ".join(linea.nombre for linea in self.cfg.lineas)
        )

        try:
            self.emisor.autenticar()
        except Exception as exc:
            # No es fatal: se sigue midiendo y encolando. La medicion es lo que
            # no se puede recuperar; el envio si.
            registro.warning(
                "No se pudo autenticar al inicio (%s). Se continua midiendo y encolando.", exc
            )

        self.captura.iniciar()
        hilo_envio = threading.Thread(target=self._bucle_envio, daemon=True, name="envio")
        hilo_envio.start()

        pendientes_al_inicio = self.cola.pendientes()
        if pendientes_al_inicio:
            registro.info(
                "Hay %s cruces pendientes de una ejecucion anterior", pendientes_al_inicio
            )

        try:
            while not self.detener.is_set():
                cuadro, momento = self.captura.leer()
                if momento is None:
                    time.sleep(0.1)
                    continue

                detecciones = self.detector.detectar(cuadro)
                cruces = self.contador.procesar(detecciones, momento)

                if cruces:
                    self.cola.encolar(cruces)
                    self.cruces_contados += len(cruces)
                    for cruce in cruces:
                        registro.debug("Cruce: %s %s", cruce.linea, cruce.direccion)

                ahora = time.monotonic()

                if ahora - self._ultimo_aforo >= self.cfg.intervalo_aforo_s:
                    self._ultimo_aforo = ahora
                    self.emisor.enviar_aforo(len(detecciones), datetime.now(UTC))

                if ahora - self._ultimo_informe >= 60:
                    self._ultimo_informe = ahora
                    self._informar_estado()

        except KeyboardInterrupt:
            registro.info("Interrupcion recibida")
        finally:
            self.cerrar()

    def _informar_estado(self) -> None:
        resumen = self.cola.resumen()
        atraso = antiguedad_minutos(resumen["mas_antiguo"], datetime.now(UTC))
        registro.info(
            "cuadros=%s | personas=%s | cruces=%s | pendientes=%s%s "
            "| conectada=%s | reconexiones=%s",
            self.captura.cuadros_leidos,
            self.contador.objetos_activos,
            self.cruces_contados,
            resumen["pendientes"],
            f" (atraso {atraso:.0f} min)" if atraso and atraso > 2 else "",
            self.captura.conectada,
            self.captura.reconexiones,
        )

    def cerrar(self) -> None:
        registro.info("Cerrando el agente")
        self.detener.set()
        self.captura.detener()

        # Ultimo intento de vaciar la cola antes de salir: si el cierre es
        # ordenado, no hay razon para dejar mediciones sin enviar.
        try:
            filas = self.cola.tomar_lote(self.cfg.tamano_lote)
            while filas:
                resultado = self.canal.enviar(filas)
                if not resultado.exitoso:
                    break
                self.cola.confirmar([fila["id"] for fila in filas])
                filas = self.cola.tomar_lote(self.cfg.tamano_lote)
        except Exception:
            registro.warning(
                "No se pudo vaciar la cola al cerrar; quedan cruces para la proxima ejecucion"
            )

        eliminados = self.cola.purgar_enviados(self.cfg.dias_retencion_cola)
        if eliminados:
            registro.info("Purgados %s cruces ya confirmados", eliminados)

        resumen = self.cola.resumen()
        registro.info(
            "Resumen final | cruces contados=%s | pendientes=%s",
            self.cruces_contados,
            resumen["pendientes"],
        )
        self.emisor.cerrar()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Agente de vision del sistema de medicion de tiempo de espera"
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument(
        "--duracion",
        type=float,
        default=None,
        help="Segundos de ejecucion. Sin este valor corre indefinidamente.",
    )
    args = parser.parse_args()

    cfg = configuracion_modulo.cargar(args.config)

    logging.basicConfig(
        level=getattr(logging, cfg.nivel_registro.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    agente = Agente(cfg)

    def manejar_senal(_num, _marco):
        registro.info("Senal de terminacion recibida")
        agente.detener.set()

    signal.signal(signal.SIGINT, manejar_senal)
    signal.signal(signal.SIGTERM, manejar_senal)

    if args.duracion:
        temporizador = threading.Timer(args.duracion, agente.detener.set)
        temporizador.daemon = True
        temporizador.start()

    agente.ejecutar()


if __name__ == "__main__":
    main()

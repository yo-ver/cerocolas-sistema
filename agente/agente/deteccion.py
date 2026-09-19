"""Deteccion y seguimiento de personas.

Se define una interfaz comun y dos implementaciones:

  DetectorYolo      usa YOLO11n preentrenado en COCO, con ByteTrack.
  DetectorSimulado  genera trayectorias sinteticas sin modelo ni camara.

La separacion no es un adorno. Permite desarrollar y probar el agente completo
—conteo, cola, envio, reconexion— en cualquier maquina, sin GPU y sin esperar
la instalacion de las camaras. En un proyecto de cuatro semanas eso decide si
el trabajo avanza en paralelo o en serie.

Sobre el modelo: se usan los pesos preentrenados y solo la clase `person` de
COCO. No hay entrenamiento propio ni conjunto de datos etiquetado, lo que
elimina la etapa mas larga de un proyecto de vision. El seguimiento solo debe
mantener la identidad durante los dos segundos que dura el cruce de la linea,
no durante las horas que el paciente permanece en la sala.

LICENCIA: la implementacion de referencia de YOLO (Ultralytics) se distribuye
bajo AGPL-3.0. Para este proyecto academico se acepta esa licencia; si el
repositorio se publica, debe licenciarse tambien como AGPL-3.0.
"""

from __future__ import annotations

import math
import random
from typing import Protocol

from agente.conteo import Deteccion

# Identificador de la clase `person` en COCO.
CLASE_PERSONA = 0


class Detector(Protocol):
    def detectar(self, cuadro) -> list[Deteccion]: ...


class DetectorYolo:
    """Detector real. Requiere `ultralytics` instalado.

    El modelo y el seguidor se cargan una sola vez; `model.track` mantiene el
    estado del seguimiento entre llamadas mientras se use `persist=True`.
    """

    def __init__(
        self,
        pesos: str = "yolo11n.pt",
        umbral_confianza: float = 0.35,
        umbral_iou: float = 0.5,
        tamano_inferencia: int = 640,
        dispositivo: str | None = None,
        seguidor: str = "bytetrack.yaml",
    ):
        try:
            from ultralytics import YOLO
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "El detector YOLO requiere el paquete `ultralytics`. "
                "Instalar con: pip install ultralytics\n"
                "Para desarrollo sin modelo, usar detector: simulado en la configuracion."
            ) from exc

        self.modelo = YOLO(pesos)
        self.umbral_confianza = umbral_confianza
        self.umbral_iou = umbral_iou
        self.tamano_inferencia = tamano_inferencia
        self.dispositivo = dispositivo
        self.seguidor = seguidor

    def detectar(self, cuadro) -> list[Deteccion]:  # pragma: no cover
        resultados = self.modelo.track(
            cuadro,
            persist=True,
            classes=[CLASE_PERSONA],
            conf=self.umbral_confianza,
            iou=self.umbral_iou,
            imgsz=self.tamano_inferencia,
            device=self.dispositivo,
            tracker=self.seguidor,
            verbose=False,
        )
        if not resultados:
            return []

        cajas = resultados[0].boxes
        if cajas is None or cajas.id is None:
            # Sin identificadores asignados no se puede detectar un cruce:
            # hace falta comparar el lado del mismo objeto entre cuadros.
            return []

        detecciones: list[Deteccion] = []
        for caja, track_id, confianza in zip(
            cajas.xyxy.tolist(), cajas.id.tolist(), cajas.conf.tolist(), strict=True
        ):
            detecciones.append(
                Deteccion(
                    track_id=int(track_id),
                    caja=(caja[0], caja[1], caja[2], caja[3]),
                    confianza=float(confianza),
                )
            )
        return detecciones


class DetectorSimulado:
    """Genera personas que se desplazan y cruzan las lineas configuradas.

    Sirve para dos cosas: probar el agente completo sin camara, y verificar en
    una demostracion que la cadena de extremo a extremo funciona.
    """

    def __init__(
        self,
        ancho: int = 1920,
        alto: int = 1080,
        y_entrada: int = 620,
        y_salida: int = 400,
        personas_por_minuto: float = 25.0,
        fps: float = 10.0,
        semilla: int = 42,
    ):
        self.ancho = ancho
        self.alto = alto
        self.y_entrada = y_entrada
        self.y_salida = y_salida
        self.fps = fps
        self.probabilidad_aparicion = personas_por_minuto / (60.0 * fps)
        self.rng = random.Random(semilla)
        self._siguiente_id = 1
        self._activos: list[dict] = []

    def _crear_persona(self) -> dict:
        # Dos poblaciones: quienes entran a la sala (suben hacia y_entrada) y
        # quienes pasan a consultorio (suben hacia y_salida).
        hacia_consultorio = self.rng.random() < 0.45
        objetivo = self.y_salida - 60 if hacia_consultorio else self.y_entrada - 60
        persona = {
            "id": self._siguiente_id,
            "x": self.rng.uniform(200, self.ancho - 200),
            "y": self.alto - 60 if not hacia_consultorio else self.y_entrada + 40,
            "objetivo": objetivo,
            "velocidad": self.rng.uniform(6, 14),
            "alto_caja": self.rng.uniform(140, 220),
            "confianza": min(0.99, max(0.55, self.rng.gauss(0.87, 0.07))),
        }
        self._siguiente_id += 1
        return persona

    def detectar(self, cuadro=None) -> list[Deteccion]:
        if self.rng.random() < self.probabilidad_aparicion:
            self._activos.append(self._crear_persona())

        detecciones: list[Deteccion] = []
        sobrevivientes: list[dict] = []

        for persona in self._activos:
            persona["y"] -= persona["velocidad"]
            persona["x"] += self.rng.uniform(-2, 2)

            if persona["y"] < persona["objetivo"] - 80:
                continue  # salio del encuadre
            sobrevivientes.append(persona)

            # Falso negativo ocasional: el detector pierde a alguien un cuadro.
            if self.rng.random() < 0.03:
                continue

            ancho_caja = persona["alto_caja"] * 0.42
            detecciones.append(
                Deteccion(
                    track_id=persona["id"],
                    caja=(
                        persona["x"] - ancho_caja / 2,
                        persona["y"] - persona["alto_caja"],
                        persona["x"] + ancho_caja / 2,
                        persona["y"],
                    ),
                    confianza=persona["confianza"],
                )
            )

        self._activos = sobrevivientes
        return detecciones


def construir_detector(configuracion: dict) -> Detector:
    tipo = configuracion.get("tipo", "yolo").lower()
    if tipo == "simulado":
        return DetectorSimulado(**configuracion.get("simulado", {}))
    if tipo == "yolo":
        return DetectorYolo(
            **{clave: valor for clave, valor in configuracion.get("yolo", {}).items()}
        )
    raise ValueError(f"Tipo de detector desconocido: {tipo}")


def distancia(p: tuple[float, float], q: tuple[float, float]) -> float:
    return math.dist(p, q)

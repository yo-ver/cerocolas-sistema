"""Deteccion de cruces de linea.

Este modulo es el corazon del agente y esta escrito sin dependencias de vision
por computadora a proposito: recibe posiciones ya detectadas y decide si
alguien cruzo una linea. Asi la logica mas delicada del sistema se puede
probar sin camara, sin GPU y sin modelo.

Como funciona. Cada linea es un segmento AB en coordenadas de imagen. Para un
punto P se calcula el signo del producto cruzado (B-A) x (P-A): indica de que
lado del segmento esta el punto. Cuando un mismo objeto seguido cambia de signo
entre dos cuadros consecutivos, y lo hace dentro del tramo del segmento, se
registra un cruce.

Dos decisiones que importan mas de lo que parece:

1. El punto de anclaje es la BASE del recuadro, no su centro. Una persona se
   detecta como un rectangulo alto; si se usa el centro, alguien alto y alguien
   bajo cruzan la linea en momentos distintos aunque pisen el mismo punto del
   piso. La base aproxima donde estan los pies, que es lo que realmente cruza
   el umbral de la puerta.

2. Se exige una separacion minima respecto del ultimo cruce del mismo objeto.
   Sin eso, una persona que se detiene justo sobre la linea genera decenas de
   cruces alternados mientras el recuadro oscila un pixel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Deteccion:
    """Una persona detectada en un cuadro, ya asociada a un identificador."""

    track_id: int
    # Recuadro en coordenadas de imagen: (x1, y1, x2, y2)
    caja: tuple[float, float, float, float]
    confianza: float

    @property
    def ancla(self) -> tuple[float, float]:
        """Punto de referencia: centro de la base del recuadro (los pies)."""
        x1, _, x2, y2 = self.caja
        return ((x1 + x2) / 2.0, y2)


@dataclass(frozen=True)
class CruceDetectado:
    linea: str
    direccion: str  # "IN" u "OUT"
    ocurrido_en: datetime
    confianza: float
    track_id: int


@dataclass
class LineaConteo:
    """Segmento de conteo con memoria del lado en que estaba cada objeto.

    CONVENCION DE DIRECCION. La linea es un segmento orientado de A hacia B.
    Quien cruza pasando hacia el lado IZQUIERDO de la flecha A->B, tal como se
    ve en pantalla, genera `sentido_positivo`; quien cruza al reves genera el
    contrario.

    Se eligio una convencion visual y no algebraica porque quien calibra es una
    persona mirando el video durante la visita tecnica, no un programa. La
    herramienta `calibrar_lineas.py` dibuja la flecha y rotula de que lado cae
    cada direccion, de modo que la decision se toma viendo, no razonando sobre
    signos. Conviene recordar que en coordenadas de imagen el eje vertical
    crece hacia abajo, asi que la intuicion algebraica se invierte y es una
    fuente clasica de lineas configuradas al reves.
    """

    nombre: str
    a: tuple[float, float]
    b: tuple[float, float]
    sentido_positivo: str = "IN"
    # Separacion minima entre dos cruces del mismo objeto.
    espera_minima: timedelta = timedelta(seconds=2)

    _lado_previo: dict[int, int] = field(default_factory=dict, repr=False)
    _ultimo_cruce: dict[int, datetime] = field(default_factory=dict, repr=False)

    def _lado(self, punto: tuple[float, float]) -> int:
        """Lado del segmento: +1 a la izquierda de A->B en pantalla, -1 a la derecha.

        El producto cruzado da el signo contrario porque el eje vertical de la
        imagen crece hacia abajo; se invierte aqui para que el resto del modulo
        razone en terminos de lo que se ve.
        """
        (ax, ay), (bx, by) = self.a, self.b
        px, py = punto
        producto = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
        if producto < 0:
            return 1
        if producto > 0:
            return -1
        return 0

    def _dentro_del_tramo(self, punto: tuple[float, float]) -> bool:
        """Comprueba que la proyeccion del punto cae dentro del segmento.

        Sin esta comprobacion, la linea se comportaria como una recta infinita
        y contaria a personas que pasan lejos de la puerta, del otro extremo
        de la sala, simplemente por estar del otro lado de la prolongacion.
        """
        (ax, ay), (bx, by) = self.a, self.b
        px, py = punto
        dx, dy = bx - ax, by - ay
        longitud2 = dx * dx + dy * dy
        if longitud2 == 0:
            return False
        t = ((px - ax) * dx + (py - ay) * dy) / longitud2
        return 0.0 <= t <= 1.0

    def procesar(self, detecciones: list[Deteccion], momento: datetime) -> list[CruceDetectado]:
        """Actualiza el estado con un cuadro y devuelve los cruces ocurridos."""
        cruces: list[CruceDetectado] = []
        vistos: set[int] = set()

        for deteccion in detecciones:
            vistos.add(deteccion.track_id)
            punto = deteccion.ancla
            lado = self._lado(punto)
            if lado == 0:
                continue  # exactamente sobre la linea: se espera al cuadro siguiente

            previo = self._lado_previo.get(deteccion.track_id)
            self._lado_previo[deteccion.track_id] = lado

            if previo is None or previo == lado:
                continue
            if not self._dentro_del_tramo(punto):
                continue

            ultimo = self._ultimo_cruce.get(deteccion.track_id)
            if ultimo is not None and momento - ultimo < self.espera_minima:
                continue

            direccion = (
                self.sentido_positivo
                if lado > 0
                else ("OUT" if self.sentido_positivo == "IN" else "IN")
            )
            self._ultimo_cruce[deteccion.track_id] = momento
            cruces.append(
                CruceDetectado(
                    linea=self.nombre,
                    direccion=direccion,
                    ocurrido_en=momento,
                    confianza=deteccion.confianza,
                    track_id=deteccion.track_id,
                )
            )

        return cruces

    def olvidar(self, track_ids: set[int]) -> None:
        """Libera el estado de objetos que ya no se siguen.

        Necesario porque los identificadores se reciclan: si un objeto nuevo
        recibe el identificador de uno viejo, heredaria su lado previo y
        generaria un cruce falso en su primer cuadro.
        """
        for track_id in track_ids:
            self._lado_previo.pop(track_id, None)
            self._ultimo_cruce.pop(track_id, None)


class ContadorSala:
    """Agrupa todas las lineas de una sala y limpia el estado obsoleto."""

    def __init__(self, lineas: list[LineaConteo], cuadros_para_olvidar: int = 90):
        self.lineas = lineas
        self.cuadros_para_olvidar = cuadros_para_olvidar
        self._ultimo_visto: dict[int, int] = {}
        self._contador_cuadros = 0

    def procesar(self, detecciones: list[Deteccion], momento: datetime) -> list[CruceDetectado]:
        self._contador_cuadros += 1
        for deteccion in detecciones:
            self._ultimo_visto[deteccion.track_id] = self._contador_cuadros

        cruces: list[CruceDetectado] = []
        for linea in self.lineas:
            cruces.extend(linea.procesar(detecciones, momento))

        # Cada cierto numero de cuadros se purgan los identificadores que
        # llevan tiempo sin aparecer, para que la memoria no crezca durante
        # una jornada de ocho horas.
        if self._contador_cuadros % 300 == 0:
            caducados = {
                track_id
                for track_id, visto in self._ultimo_visto.items()
                if self._contador_cuadros - visto > self.cuadros_para_olvidar
            }
            for track_id in caducados:
                self._ultimo_visto.pop(track_id, None)
            for linea in self.lineas:
                linea.olvidar(caducados)

        return cruces

    @property
    def objetos_activos(self) -> int:
        return len(self._ultimo_visto)

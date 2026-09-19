"""Notificacion de eventos de ingesta.

PATRONES APLICADOS
------------------
* Observer (GoF, comportamiento): el endpoint de ingesta publica lo que ocurrio
  y no sabe quien reacciona. Los interesados —registro de operacion, deteccion
  de saturacion, alimentacion del tablero en vivo— se suscriben.

POR QUE ESTE PATRON
-------------------
El endpoint de ingesta es la ruta caliente del sistema: recibe todos los cruces
de ambos hospitales durante toda la jornada. Cada responsabilidad que se le
anada la vuelve mas lenta y mas fragil, y varias de las que ya se preveen
—avisar a la jefatura cuando la sala se satura, empujar actualizaciones al
tablero, registrar la tasa de rechazo por agente— no tienen nada que ver con
almacenar cruces.

Sin Observer, cada una de esas funciones seria un bloque mas dentro del
endpoint, y un fallo en cualquiera de ellas haria fallar la ingesta. Con
Observer, la ingesta publica y sigue; si un observador falla, se registra y no
se propaga, porque perder un aviso es aceptable y perder una medicion no.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

registro = logging.getLogger("app.eventos")


@dataclass(frozen=True)
class EventoLoteIngerido:
    """Lo ocurrido al procesar un lote de cruces."""

    sala_codigo: str
    lote_id: str
    agente: str | None
    recibidos: int
    aceptados: int
    rechazados: int
    duplicado: bool
    momento: datetime
    motivos: list[str] = field(default_factory=list)

    @property
    def tasa_rechazo(self) -> float:
        return self.rechazados / self.recibidos if self.recibidos else 0.0


class ObservadorIngesta(ABC):
    """Interfaz de los suscriptores."""

    nombre: str = "observador"

    @abstractmethod
    def notificar(self, evento: EventoLoteIngerido) -> None: ...


class RegistroDeOperacion(ObservadorIngesta):
    """Deja constancia en el registro de la aplicacion."""

    nombre = "registro_operacion"

    def notificar(self, evento: EventoLoteIngerido) -> None:
        if evento.duplicado:
            registro.info(
                "Lote duplicado reconocido | sala=%s agente=%s",
                evento.sala_codigo,
                evento.agente,
            )
            return
        registro.info(
            "Lote ingerido | sala=%s agente=%s aceptados=%s rechazados=%s",
            evento.sala_codigo,
            evento.agente,
            evento.aceptados,
            evento.rechazados,
        )


class DetectorDeCalibracion(ObservadorIngesta):
    """Avisa cuando la tasa de rechazo sugiere un problema de instalacion.

    Un lote con rechazos aislados es ruido normal del detector. Un lote donde
    casi todo se rechaza significa otra cosa: casi siempre una linea trazada al
    reves durante la visita tecnica. Detectarlo el primer dia evita perder una
    jornada entera de mediciones.
    """

    nombre = "detector_calibracion"

    def __init__(self, umbral: float = 0.5, minimo_eventos: int = 10) -> None:
        self.umbral = umbral
        # Con lotes pequenos la tasa es demasiado volatil para concluir nada.
        self.minimo_eventos = minimo_eventos

    def notificar(self, evento: EventoLoteIngerido) -> None:
        if evento.duplicado or evento.recibidos < self.minimo_eventos:
            return
        if evento.tasa_rechazo < self.umbral:
            return

        motivo = evento.motivos[0] if evento.motivos else "sin detalle"
        registro.warning(
            "Posible error de calibracion en %s: se rechazo el %.0f %% del lote. Primer motivo: %s",
            evento.sala_codigo,
            evento.tasa_rechazo * 100,
            motivo,
        )


class ContadorDeActividad(ObservadorIngesta):
    """Acumula totales por sala para el estado operativo del sistema.

    Vive en memoria a proposito: es informacion de diagnostico del proceso, no
    un dato del dominio. Los datos del dominio estan en la base y se derivan de
    los cruces; duplicarlos aqui abriria la puerta a que ambas fuentes
    divergieran.
    """

    nombre = "contador_actividad"

    def __init__(self) -> None:
        self.por_sala: dict[str, dict[str, int | str | None]] = {}

    def notificar(self, evento: EventoLoteIngerido) -> None:
        if evento.duplicado:
            return
        actual = self.por_sala.setdefault(
            evento.sala_codigo,
            {"lotes": 0, "aceptados": 0, "rechazados": 0, "ultimo": None},
        )
        actual["lotes"] = int(actual["lotes"]) + 1
        actual["aceptados"] = int(actual["aceptados"]) + evento.aceptados
        actual["rechazados"] = int(actual["rechazados"]) + evento.rechazados
        actual["ultimo"] = evento.momento.isoformat()

    def resumen(self) -> dict:
        return {sala: dict(datos) for sala, datos in self.por_sala.items()}


class PublicadorIngesta:
    """El sujeto observable.

    Mantiene la lista de suscriptores y les entrega el evento. Aisla los fallos
    de cada observador: la ingesta no puede caerse porque un aviso no se pudo
    enviar.
    """

    def __init__(self) -> None:
        self._observadores: list[ObservadorIngesta] = []

    def suscribir(self, observador: ObservadorIngesta) -> None:
        if observador not in self._observadores:
            self._observadores.append(observador)

    def cancelar(self, observador: ObservadorIngesta) -> None:
        if observador in self._observadores:
            self._observadores.remove(observador)

    @property
    def suscriptores(self) -> list[str]:
        return [observador.nombre for observador in self._observadores]

    def publicar(self, evento: EventoLoteIngerido) -> None:
        for observador in self._observadores:
            try:
                observador.notificar(evento)
            except Exception:
                # Deliberadamente amplio: un observador defectuoso no puede
                # impedir que los demas se enteren ni hacer fallar la peticion.
                registro.exception(
                    "El observador %s fallo al procesar el evento", observador.nombre
                )


# Instancia compartida por la aplicacion, con los observadores por defecto.
# Es un Singleton de modulo, la forma idiomatica en Python: la unicidad la
# garantiza el sistema de importacion, sin necesidad de una clase que se
# controle a si misma.
contador_actividad = ContadorDeActividad()

publicador = PublicadorIngesta()
publicador.suscribir(RegistroDeOperacion())
publicador.suscribir(DetectorDeCalibracion())
publicador.suscribir(contador_actividad)

"""Validacion de los cruces recibidos del agente de vision.

PATRONES APLICADOS
------------------
* Chain of Responsibility (GoF, comportamiento): cada regla es un eslabon
  independiente que examina el cruce y decide si lo rechaza o lo cede al
  siguiente. El endpoint de ingesta ya no contiene la logica de validacion:
  solo recorre la cadena.
* Template Method (GoF, comportamiento): `ReglaValidacion` fija el mecanismo de
  encadenamiento y delega en `evaluar` la comprobacion concreta, de modo que
  una regla nueva no puede olvidarse de propagar la peticion.

POR QUE ESTE PATRON Y NO UNA LISTA DE FUNCIONES
-----------------------------------------------
El orden de las comprobaciones importa y debe quedar explicito. La existencia
de la linea se verifica antes que la coherencia de la direccion, porque sin
linea no hay tipo con el que comparar. La coherencia se verifica antes que el
horario, porque un cruce con la geometria invertida es un defecto de
calibracion que conviene reportar aunque tambien este fuera de horario, y
reportar el sintoma menos util confundiria al operador.

Una cadena hace ese orden visible en la construccion. Ademas permite que cada
sala tenga reglas distintas —una sala con horario nocturno no puede compartir
la regla de horario con una diurna— sin bifurcaciones dentro del endpoint.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, time, timedelta


@dataclass(frozen=True)
class ContextoCruce:
    """Un cruce en evaluacion, con lo necesario para juzgarlo."""

    indice: int
    linea_nombre: str
    direccion: str
    ocurrido_en: datetime
    confianza: float | None
    #: Linea configurada, o None si el nombre no corresponde a ninguna.
    linea: object | None
    recibido_en: datetime
    hora_apertura: time | None = None
    hora_cierre: time | None = None


@dataclass(frozen=True)
class Rechazo:
    indice: int
    motivo: str
    regla: str


class ReglaValidacion(ABC):
    """Eslabon de la cadena.

    `validar` es el metodo plantilla: gestiona el encadenamiento y no se
    sobrescribe. Las subclases implementan `evaluar`, que devuelve el motivo
    del rechazo o None si el cruce le parece aceptable.
    """

    nombre: str = "regla"

    def __init__(self) -> None:
        self._siguiente: ReglaValidacion | None = None

    def encadenar(self, siguiente: ReglaValidacion) -> ReglaValidacion:
        """Enlaza el siguiente eslabon y lo devuelve, para permitir fluidez."""
        self._siguiente = siguiente
        return siguiente

    def validar(self, contexto: ContextoCruce) -> Rechazo | None:
        motivo = self.evaluar(contexto)
        if motivo is not None:
            # Se detiene en el primer rechazo: reportar todos los motivos de un
            # mismo cruce no aporta nada al operador, que corrige uno a la vez.
            return Rechazo(indice=contexto.indice, motivo=motivo, regla=self.nombre)
        if self._siguiente is not None:
            return self._siguiente.validar(contexto)
        return None

    @abstractmethod
    def evaluar(self, contexto: ContextoCruce) -> str | None:
        """Motivo del rechazo, o None si el cruce pasa esta regla."""


class LineaDebeExistir(ReglaValidacion):
    """Primer eslabon: sin linea configurada no hay nada que comprobar."""

    nombre = "linea_existe"

    def evaluar(self, contexto: ContextoCruce) -> str | None:
        if contexto.linea is None:
            return f"Linea desconocida: {contexto.linea_nombre}"
        return None


class DireccionDebeSerCoherente(ReglaValidacion):
    """La direccion debe corresponder al tipo de linea configurado.

    Una linea de ENTRADA que reporta salidas indica que la flecha A->B se
    trazo al reves durante la calibracion. Es el error de instalacion mas
    frecuente, y el motivo del rechazo es literalmente la instruccion para
    corregirlo.
    """

    nombre = "direccion_coherente"

    def evaluar(self, contexto: ContextoCruce) -> str | None:
        tipo = getattr(contexto.linea, "tipo", None)
        esperada = "IN" if tipo == "ENTRADA" else "OUT"
        if contexto.direccion != esperada:
            return (
                f"La linea {contexto.linea_nombre} es de tipo {tipo} "
                f"y solo admite direccion {esperada}"
            )
        return None


class NoPuedeVenirDelFuturo(ReglaValidacion):
    """Un instante futuro delata el reloj del equipo de borde desajustado.

    Se tolera un margen de cinco minutos: el desfase natural entre dos relojes
    no sincronizados esta muy por debajo de eso, y rechazar por segundos
    convertiria un problema de precision en una perdida de mediciones.
    """

    nombre = "sin_futuro"

    def __init__(self, margen: timedelta = timedelta(minutes=5)) -> None:
        super().__init__()
        self.margen = margen

    def evaluar(self, contexto: ContextoCruce) -> str | None:
        if contexto.ocurrido_en > contexto.recibido_en + self.margen:
            return "ocurrido_en esta en el futuro"
        return None


class ConfianzaMinima(ReglaValidacion):
    """Descarta detecciones por debajo del umbral configurado.

    No se aplica por defecto. Existe porque una camara mal enfocada produce
    detecciones de confianza baja y sistematicamente erronea, y filtrarlas en
    el servidor permite mitigar el problema mientras se coordina la visita
    tecnica, sin tener que actualizar el agente en el hospital.
    """

    nombre = "confianza_minima"

    def __init__(self, umbral: float = 0.0) -> None:
        super().__init__()
        self.umbral = umbral

    def evaluar(self, contexto: ContextoCruce) -> str | None:
        if self.umbral <= 0 or contexto.confianza is None:
            return None
        if contexto.confianza < self.umbral:
            return f"Confianza {contexto.confianza:.2f} por debajo del umbral {self.umbral:.2f}"
        return None


class DentroDelHorario(ReglaValidacion):
    """Rechaza cruces fuera del horario declarado de la sala.

    No se aplica por defecto porque las colas de estos hospitales empiezan
    antes de la apertura formal y contar a quien ya espera es precisamente el
    objetivo. Se ofrece para salas donde el encuadre de la camara incluya una
    zona de transito nocturno.
    """

    nombre = "dentro_horario"

    def __init__(self, holgura_horas: float = 1.0) -> None:
        super().__init__()
        self.holgura = timedelta(hours=holgura_horas)

    def evaluar(self, contexto: ContextoCruce) -> str | None:
        if contexto.hora_apertura is None or contexto.hora_cierre is None:
            return None

        momento = contexto.ocurrido_en.time()
        apertura = (
            datetime.combine(contexto.ocurrido_en.date(), contexto.hora_apertura) - self.holgura
        ).time()
        cierre = (
            datetime.combine(contexto.ocurrido_en.date(), contexto.hora_cierre) + self.holgura
        ).time()

        if momento < apertura or momento > cierre:
            return (
                f"Fuera del horario de la sala ({contexto.hora_apertura} a {contexto.hora_cierre})"
            )
        return None


def construir_cadena(
    confianza_minima: float = 0.0,
    exigir_horario: bool = False,
) -> ReglaValidacion:
    """Arma la cadena en el orden en que las reglas deben aplicarse.

    El orden esta fijado aqui y no en cada regla: es una propiedad de la
    cadena, no de sus eslabones, y tenerlo en un solo lugar es lo que permite
    razonar sobre el.
    """
    primera = LineaDebeExistir()
    actual = primera.encadenar(DireccionDebeSerCoherente())
    actual = actual.encadenar(NoPuedeVenirDelFuturo())

    if confianza_minima > 0:
        actual = actual.encadenar(ConfianzaMinima(confianza_minima))
    if exigir_horario:
        actual = actual.encadenar(DentroDelHorario())

    return primera

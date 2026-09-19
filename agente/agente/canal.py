"""Envoltorios del emisor de cruces.

PATRONES APLICADOS
------------------
* Decorator (GoF, estructural): cada preocupacion transversal del envio
  —reintentar, medir, registrar— se implementa como un envoltorio que expone la
  misma interfaz que envuelve. Se componen en el orden que convenga y el emisor
  base queda reducido a lo unico que le corresponde: construir la peticion y
  enviarla.
* Adapter (GoF, estructural): `EnvioResultado` normaliza lo que devuelven las
  distintas capas en un unico tipo, de modo que quien consume el envio no tiene
  que distinguir entre None, un diccionario y una excepcion.

POR QUE DECORATOR Y NO MAS PARAMETROS EN EL EMISOR
---------------------------------------------------
El emisor original tenia el reintento cableado dentro del metodo de envio: un
bucle con espera creciente mezclado con la construccion del cuerpo de la
peticion. Funcionaba, pero tenia dos consecuencias.

La primera es de prueba. Verificar la politica de reintento obligaba a simular
un servidor, porque no habia forma de ejercitarla en aislamiento. La segunda es
de configuracion. Durante la calibracion en campo interesa que el agente falle
rapido y muestre el error; en operacion interesa lo contrario, que insista en
silencio. Con la politica dentro del emisor eso son ramas; con Decorator son
dos composiciones distintas del mismo objeto.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

registro = logging.getLogger("agente.envio")


@dataclass
class EnvioResultado:
    """Resultado normalizado de un intento de envio."""

    exitoso: bool
    aceptados: int = 0
    rechazados: int = 0
    duplicado: bool = False
    intentos: int = 1
    motivo: str | None = None
    detalles: list[str] = field(default_factory=list)

    @classmethod
    def desde_respuesta(cls, datos: dict | None) -> EnvioResultado:
        if datos is None:
            return cls(exitoso=False, motivo="el servidor no acepto el lote")
        return cls(
            exitoso=True,
            aceptados=datos.get("aceptados", 0),
            rechazados=datos.get("rechazados", 0),
            duplicado=bool(datos.get("duplicado")),
            detalles=[d.get("motivo", "") for d in (datos.get("detalles") or [])],
        )


class CanalEnvio(ABC):
    """Interfaz comun al emisor y a todos sus envoltorios."""

    @abstractmethod
    def enviar(self, filas: list[dict]) -> EnvioResultado: ...


class CanalDirecto(CanalEnvio):
    """Componente concreto: un unico intento, sin politica alguna.

    Es deliberadamente escueto. Toda la logica de resiliencia vive en los
    decoradores, de modo que este objeto se puede leer entero de un vistazo y
    probar sin simular fallos.
    """

    def __init__(self, emisor) -> None:
        self.emisor = emisor

    def enviar(self, filas: list[dict]) -> EnvioResultado:
        return EnvioResultado.desde_respuesta(self.emisor.enviar_lote(filas))


class DecoradorCanal(CanalEnvio):
    """Base de los envoltorios. Delega por defecto."""

    def __init__(self, envuelto: CanalEnvio) -> None:
        self._envuelto = envuelto

    def enviar(self, filas: list[dict]) -> EnvioResultado:
        return self._envuelto.enviar(filas)


class ConReintentos(DecoradorCanal):
    """Reintenta con espera creciente ante un fallo transitorio.

    La espera crece porque una red que acaba de fallar no mejora si se la
    golpea con la misma frecuencia. El tope existe para que un corte largo no
    lleve la espera a valores en que el agente pareceria colgado.
    """

    def __init__(
        self,
        envuelto: CanalEnvio,
        intentos: int = 5,
        espera_inicial: float = 2.0,
        espera_maxima: float = 60.0,
        dormir=time.sleep,
    ) -> None:
        super().__init__(envuelto)
        self.intentos = max(1, intentos)
        self.espera_inicial = espera_inicial
        self.espera_maxima = espera_maxima
        # Inyectable para que las pruebas no tengan que esperar de verdad.
        self._dormir = dormir

    def enviar(self, filas: list[dict]) -> EnvioResultado:
        espera = self.espera_inicial
        ultimo = EnvioResultado(exitoso=False, motivo="sin intentos")

        for intento in range(1, self.intentos + 1):
            resultado = self._envuelto.enviar(filas)
            resultado.intentos = intento
            if resultado.exitoso:
                return resultado

            ultimo = resultado
            if intento < self.intentos:
                registro.warning(
                    "Envio fallido (intento %s de %s): %s",
                    intento,
                    self.intentos,
                    resultado.motivo,
                )
                self._dormir(espera)
                espera = min(espera * 2, self.espera_maxima)

        ultimo.intentos = self.intentos
        return ultimo


class ConMetricas(DecoradorCanal):
    """Acumula totales de envio para el informe de estado del agente.

    Sin esto, saber cuantos cruces logro entregar un agente durante una jornada
    exigiria leer el registro linea por linea.
    """

    def __init__(self, envuelto: CanalEnvio) -> None:
        super().__init__(envuelto)
        self.lotes_enviados = 0
        self.lotes_fallidos = 0
        self.cruces_aceptados = 0
        self.cruces_rechazados = 0
        self.duplicados = 0
        self.intentos_totales = 0

    def enviar(self, filas: list[dict]) -> EnvioResultado:
        resultado = self._envuelto.enviar(filas)
        self.intentos_totales += resultado.intentos

        if resultado.exitoso:
            self.lotes_enviados += 1
            self.cruces_aceptados += resultado.aceptados
            self.cruces_rechazados += resultado.rechazados
            if resultado.duplicado:
                self.duplicados += 1
        else:
            self.lotes_fallidos += 1

        return resultado

    def resumen(self) -> dict:
        return {
            "lotes_enviados": self.lotes_enviados,
            "lotes_fallidos": self.lotes_fallidos,
            "cruces_aceptados": self.cruces_aceptados,
            "cruces_rechazados": self.cruces_rechazados,
            "lotes_duplicados": self.duplicados,
            "intentos_totales": self.intentos_totales,
        }


class ConRegistroDetallado(DecoradorCanal):
    """Registra cada envio. Util durante la calibracion en campo.

    No se aplica en operacion: escribiria una linea por lote durante ocho horas
    y ahogaria los avisos que si importan.
    """

    def enviar(self, filas: list[dict]) -> EnvioResultado:
        registro.info("Enviando lote de %s cruces", len(filas))
        resultado = self._envuelto.enviar(filas)
        if resultado.exitoso:
            registro.info(
                "Lote entregado | aceptados=%s rechazados=%s duplicado=%s intentos=%s",
                resultado.aceptados,
                resultado.rechazados,
                resultado.duplicado,
                resultado.intentos,
            )
        else:
            registro.error("Lote no entregado tras %s intentos", resultado.intentos)
        return resultado


def construir_canal(
    emisor,
    intentos: int = 5,
    con_metricas: bool = True,
    detallado: bool = False,
    dormir=time.sleep,
) -> CanalEnvio:
    """Compone la pila de decoradores.

    El orden importa y esta fijado aqui a proposito. Las metricas envuelven al
    reintento, no al reves: interesa contar un lote una sola vez aunque haya
    necesitado cuatro intentos, y registrar cuantos intentos costo.
    """
    canal: CanalEnvio = CanalDirecto(emisor)
    canal = ConReintentos(canal, intentos=intentos, dormir=dormir)
    if con_metricas:
        canal = ConMetricas(canal)
    if detallado:
        canal = ConRegistroDetallado(canal)
    return canal

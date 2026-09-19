"""Motor de curvas de flujo acumulado.

Es el nucleo metodologico del sistema. A partir de cruces anonimos reconstruye
la ocupacion de la sala y el tiempo de espera, sin identificar a nadie.

    N_in(t)  = numero acumulado de personas que entraron hasta t
    N_out(t) = numero acumulado de personas que pasaron a consultorio hasta t

    L(t)  = N_in(t) - N_out(t)                      -> personas esperando
    W(k)  = t_salida(k) - t_entrada(k)              -> espera del k-esimo

La correccion por acompanantes es indispensable. Un acompanante genera un cruce
de entrada pero nunca uno de salida, de modo que la curva de entradas queda
inflada y la espera estimada se sobrestima de forma sistematica. Si el factor
de correccion es r, la persona que sale en la posicion k corresponde a la
entrada en la posicion k*(1+r).

IMPORTANTE sobre el significado de r. Aunque se le llame "ratio de
acompanantes", en la practica absorbe toda fuente de entradas sin salida
correspondiente:

  - acompanantes que entran a la sala y nunca pasan a consultorio,
  - pacientes que abandonan o quedan sin atencion al cierre de la jornada,
  - personal que cruza la linea de entrada,
  - violaciones del orden de llegada por prioridad, que desplazan el
    emparejamiento posicional.

Por eso r es un FACTOR DE CORRECCION EFECTIVO y no un conteo. En las pruebas
con datos sinteticos, dos salas con identica proporcion real de acompanantes
(0,34) requirieron factores distintos —0,27 en la sala saturada y 0,34 en la
menos congestionada— porque la saturacion agrega abandono y amplifica el efecto
de las prioridades. La consecuencia practica es que r debe AJUSTARSE POR SALA
contra observacion manual, y no fijarse contando acompanantes en la puerta.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ResultadoCurvas:
    """Resultado del calculo para una sala y una fecha operativa."""

    n_entradas: int = 0
    n_salidas: int = 0
    ocupacion_final: int = 0
    ratio_acompanante: float = 0.0
    esperas_min: list[float] = field(default_factory=list)
    pares: list[tuple[int, datetime, datetime, float]] = field(default_factory=list)
    descartados: int = 0

    @property
    def p50(self) -> float | None:
        return percentil(self.esperas_min, 0.50)

    @property
    def p90(self) -> float | None:
        return percentil(self.esperas_min, 0.90)

    @property
    def promedio(self) -> float | None:
        if not self.esperas_min:
            return None
        return sum(self.esperas_min) / len(self.esperas_min)


def percentil(valores: list[float], q: float) -> float | None:
    """Percentil por interpolacion lineal. Devuelve None si no hay datos.

    Se reportan percentiles y no el promedio porque la distribucion de esperas
    tiene cola derecha larga: en la base manual la media era el doble de la
    mediana por efecto de unos pocos casos extremos.
    """
    if not valores:
        return None
    v = sorted(valores)
    if len(v) == 1:
        return v[0]
    pos = (len(v) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return v[int(lo)]
    return v[int(lo)] + (v[int(hi)] - v[int(lo)]) * (pos - lo)


def calcular(
    entradas: list[datetime],
    salidas: list[datetime],
    ratio_acompanante: float = 0.0,
    espera_maxima_min: float = 480.0,
) -> ResultadoCurvas:
    """Empareja entradas con salidas bajo orden de llegada y deriva las esperas.

    `entradas` y `salidas` son instantes de cruce, no personas identificadas.
    El emparejamiento es posicional: el k-esimo que sale corresponde al
    k*(1+r)-esimo que entro. Nadie es seguido individualmente.
    """
    entradas = sorted(entradas)
    salidas = sorted(salidas)

    resultado = ResultadoCurvas(
        n_entradas=len(entradas),
        n_salidas=len(salidas),
        ocupacion_final=max(0, len(entradas) - len(salidas)),
        ratio_acompanante=ratio_acompanante,
    )

    factor = 1.0 + max(0.0, ratio_acompanante)

    for k, t_salida in enumerate(salidas):
        idx = round(k * factor)
        if idx >= len(entradas):
            break
        t_entrada = entradas[idx]
        espera = (t_salida - t_entrada).total_seconds() / 60.0

        # Una espera negativa significa que las curvas se cruzaron: casi
        # siempre indica geometria de linea mal calibrada o cruces perdidos.
        # Una espera desmedida suele ser un cruce espurio. En ambos casos se
        # descarta el par, pero se cuenta para poder reportar la tasa.
        if espera < 0 or espera > espera_maxima_min:
            resultado.descartados += 1
            continue

        resultado.esperas_min.append(espera)
        resultado.pares.append((k, t_entrada, t_salida, espera))

    return resultado


def ocupacion_en(entradas: list[datetime], salidas: list[datetime], t: datetime) -> int:
    """Personas presentes en la sala en el instante t: L(t) = N_in(t) - N_out(t)."""
    entradas = sorted(entradas)
    salidas = sorted(salidas)
    return max(0, bisect_right(entradas, t) - bisect_right(salidas, t))


def serie_acumulada(
    entradas: list[datetime],
    salidas: list[datetime],
    paso_min: int = 5,
) -> list[dict]:
    """Serie temporal de N_in, N_out y ocupacion, para graficar en el tablero."""
    if not entradas and not salidas:
        return []

    entradas = sorted(entradas)
    salidas = sorted(salidas)
    inicio = min(entradas[0] if entradas else salidas[0], salidas[0] if salidas else entradas[0])
    fin = max(entradas[-1] if entradas else salidas[-1], salidas[-1] if salidas else entradas[-1])

    puntos: list[dict] = []
    paso = paso_min * 60
    total = int((fin - inicio).total_seconds() // paso) + 1
    for i in range(total + 1):
        t = inicio.fromtimestamp(inicio.timestamp() + i * paso, tz=inicio.tzinfo)
        n_in = bisect_right(entradas, t)
        n_out = bisect_right(salidas, t)
        puntos.append(
            {
                "t": t,
                "n_in": n_in,
                "n_out": n_out,
                "ocupacion": max(0, n_in - n_out),
            }
        )
    return puntos


def espera_estimada_actual(
    entradas: list[datetime],
    salidas: list[datetime],
    ahora: datetime,
    ratio_acompanante: float = 0.0,
    ventana_min: int = 60,
) -> float | None:
    """Espera que enfrentaria alguien que llegara ahora.

    Se aplica la ley de Little sobre una ventana movil reciente: W = L / lambda,
    donde L es la ocupacion actual y lambda la tasa de atencion observada en la
    ultima hora. Se usa la ventana reciente y no el dia completo porque la tasa
    de atencion cambia mucho entre el arranque y el resto de la jornada.
    """
    ocupacion = ocupacion_en(entradas, salidas, ahora)
    if ocupacion == 0:
        return 0.0

    desde = ahora.fromtimestamp(ahora.timestamp() - ventana_min * 60, tz=ahora.tzinfo)
    atendidos = sum(1 for s in salidas if desde <= s <= ahora)
    if atendidos == 0:
        return None  # sin atencion reciente no hay tasa que aplicar

    tasa_por_min = atendidos / ventana_min
    ocupacion_pacientes = ocupacion / (1.0 + max(0.0, ratio_acompanante))
    return ocupacion_pacientes / tasa_por_min

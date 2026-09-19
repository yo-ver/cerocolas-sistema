"""Pruebas del motor de curvas de flujo acumulado.

Es el componente cuyo error se traduce directamente en un indicador equivocado,
asi que se prueba contra escenarios de espera conocida.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from itertools import pairwise

from app.servicios import curvas

TZ = timezone(timedelta(hours=-5))
BASE = datetime(2026, 8, 10, 6, 0, tzinfo=TZ)


def m(minutos: float) -> datetime:
    return BASE + timedelta(minutes=minutos)


def test_espera_conocida_sin_acompanantes():
    """Tres personas entran cada 10 min y salen 30 min despues de entrar."""
    entradas = [m(0), m(10), m(20)]
    salidas = [m(30), m(40), m(50)]

    resultado = curvas.calcular(entradas, salidas)

    assert resultado.esperas_min == [30.0, 30.0, 30.0]
    assert resultado.p50 == 30.0
    assert resultado.ocupacion_final == 0


def test_ocupacion_instantanea():
    """L(t) = N_in(t) - N_out(t)."""
    entradas = [m(0), m(5), m(10), m(15)]
    salidas = [m(20), m(25)]

    assert curvas.ocupacion_en(entradas, salidas, m(12)) == 3
    assert curvas.ocupacion_en(entradas, salidas, m(22)) == 3
    assert curvas.ocupacion_en(entradas, salidas, m(30)) == 2
    # Antes de la primera llegada la sala esta vacia.
    assert curvas.ocupacion_en(entradas, salidas, m(-5)) == 0


def test_correccion_por_acompanantes():
    """Los acompanantes inflan la curva de entradas y sobrestiman la espera.

    Escenario: 4 pacientes, cada uno con un acompanante (ratio 1.0). Solo los
    pacientes cruzan a consultorio. Sin correccion, el sistema empareja al que
    sale con la entrada equivocada y sobrestima.
    """
    entradas = []
    for i in range(4):
        entradas.append(m(i * 10))  # paciente
        entradas.append(m(i * 10 + 0.5))  # acompanante
    salidas = [m(i * 10 + 60) for i in range(4)]

    ingenuo = curvas.calcular(entradas, salidas, ratio_acompanante=0.0)
    corregido = curvas.calcular(entradas, salidas, ratio_acompanante=1.0)

    # La espera real de cada paciente es de 60 minutos: la correccion la
    # recupera exactamente.
    assert corregido.p50 == 60.0

    # Sin correccion, cada salida se empareja con la entrada equivocada y la
    # estimacion se infla. Con este escenario el sesgo es de casi 10 minutos
    # sobre 60, algo mas del 16 %. Con el ratio real observado en campo el
    # efecto es aun mayor porque las llegadas estan mas separadas.
    assert ingenuo.p50 > corregido.p50
    assert ingenuo.p50 == 69.75


def test_descarta_esperas_imposibles():
    """Una espera negativa o desmedida se descarta y se contabiliza.

    La base manual de KoboCollect contenia un registro de 10 110 minutos; el
    motor no debe dejar pasar ese tipo de valor a los indicadores.
    """
    entradas = [m(0), m(10)]
    salidas = [m(30), m(10_000)]

    resultado = curvas.calcular(entradas, salidas, espera_maxima_min=480)

    assert resultado.esperas_min == [30.0]
    assert resultado.descartados == 1


def test_percentiles():
    valores = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    assert curvas.percentil(valores, 0.50) == 55.0
    assert curvas.percentil(valores, 0.90) == 91.0
    assert curvas.percentil([], 0.5) is None
    assert curvas.percentil([42], 0.9) == 42


def test_serie_acumulada_es_monotona():
    entradas = [m(i * 3) for i in range(20)]
    salidas = [m(20 + i * 4) for i in range(15)]

    puntos = curvas.serie_acumulada(entradas, salidas, paso_min=5)

    assert puntos, "la serie no debe estar vacia"
    for anterior, siguiente in pairwise(puntos):
        assert siguiente["n_in"] >= anterior["n_in"]
        assert siguiente["n_out"] >= anterior["n_out"]
        assert siguiente["ocupacion"] >= 0


def test_espera_estimada_por_ley_de_little():
    """W = L / lambda sobre la ventana movil reciente."""
    # 30 personas presentes, 20 atendidas en la ultima hora -> 90 min de espera.
    entradas = [m(-120 + i) for i in range(50)]
    salidas = [m(-59 + i * 3) for i in range(20)]
    ahora = m(0)

    estimada = curvas.espera_estimada_actual(entradas, salidas, ahora)

    assert estimada is not None
    assert 85 <= estimada <= 95


def test_sin_atencion_reciente_no_estima():
    """Si nadie fue atendido en la ventana, no hay tasa que aplicar."""
    entradas = [m(-30 + i) for i in range(10)]
    estimada = curvas.espera_estimada_actual(entradas, [], m(0))
    assert estimada is None


def test_sala_vacia_espera_cero():
    assert curvas.espera_estimada_actual([], [], m(0)) == 0.0

"""Pruebas del manejo de fechas y de la jornada operativa.

La fecha operativa es una fuente silenciosa de error: un cruce registrado a las
5:40 de la madrugada pertenece a la jornada de ese dia, no a la anterior, y una
implementacion ingenua que corte a medianoche lo atribuiria mal. Como el
indicador se agrega por fecha, ese desplazamiento no produce ningun fallo
visible: produce numeros equivocados.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.servicios.tiempo import (
    ZONA,
    a_local,
    a_utc,
    ahora_utc,
    fecha_operativa,
    rango_de_fecha,
)


def test_ahora_utc_tiene_zona():
    momento = ahora_utc()
    assert momento.tzinfo is not None
    assert momento.utcoffset() == timedelta(0)


def test_instante_sin_zona_se_interpreta_como_hora_local():
    """Un dato sin zona proviene de la operacion, no de UTC.

    Interpretarlo como UTC desplazaria el registro cinco horas y lo movería de
    jornada.
    """
    ingenuo = datetime(2026, 8, 15, 7, 0)
    convertido = a_utc(ingenuo)

    assert convertido.tzinfo == UTC
    assert a_local(convertido).hour == 7


def test_conversion_de_ida_y_vuelta_conserva_el_instante():
    original = datetime(2026, 8, 15, 6, 30, tzinfo=ZONA)
    assert a_local(a_utc(original)) == original


def test_instante_utc_sin_zona_se_asume_utc_al_pasar_a_local():
    ingenuo = datetime(2026, 8, 15, 12, 0)
    local = a_local(ingenuo)
    assert local.hour == 7  # 12:00 UTC son las 07:00 en Lima


def test_madrugada_pertenece_a_la_jornada_de_ese_dia():
    """Las colas de estos hospitales empiezan antes del amanecer."""
    cruce = datetime(2026, 8, 15, 5, 40, tzinfo=ZONA)
    assert fecha_operativa(a_utc(cruce)) == date(2026, 8, 15)


def test_antes_del_corte_pertenece_al_dia_anterior():
    """A la 1 de la madrugada no hay atencion: es cierre del dia previo."""
    cruce = datetime(2026, 8, 15, 1, 0, tzinfo=ZONA)
    assert fecha_operativa(a_utc(cruce)) == date(2026, 8, 14)


def test_el_corte_esta_en_las_tres_de_la_manana():
    antes = datetime(2026, 8, 15, 2, 59, tzinfo=ZONA)
    despues = datetime(2026, 8, 15, 3, 1, tzinfo=ZONA)

    assert fecha_operativa(a_utc(antes)) == date(2026, 8, 14)
    assert fecha_operativa(a_utc(despues)) == date(2026, 8, 15)


def test_rango_de_fecha_cubre_veinticuatro_horas_desde_el_corte():
    inicio, fin = rango_de_fecha(date(2026, 8, 15))

    assert fin - inicio == timedelta(days=1)
    assert a_local(inicio).hour == 3
    assert fecha_operativa(inicio) == date(2026, 8, 15)


def test_los_extremos_del_rango_caen_en_jornadas_distintas():
    inicio, fin = rango_de_fecha(date(2026, 8, 15))

    assert fecha_operativa(inicio) == date(2026, 8, 15)
    assert fecha_operativa(fin) == date(2026, 8, 16)


# --- configuracion ---------------------------------------------------------


def test_reconoce_el_motor_de_base_de_datos():
    """El sistema usa SQLite en desarrollo y PostgreSQL en produccion.

    El particionado por rango de la tabla de cruces solo existe en PostgreSQL,
    de modo que la migracion necesita distinguir el motor.
    """
    from app.config import Config

    assert Config(database_url="sqlite+pysqlite:///./x.db").es_postgres is False
    assert Config(database_url="postgresql+psycopg://u:c@h/d").es_postgres is True


def test_la_lista_de_origenes_cors_tolera_espacios_y_vacios():
    from app.config import Config

    config = Config(cors_origenes="https://a.pe, https://b.pe ,, ")
    assert config.lista_cors == ["https://a.pe", "https://b.pe"]


def test_un_solo_origen_cors_se_lee_como_lista():
    from app.config import Config

    assert Config(cors_origenes="https://uno.pe").lista_cors == ["https://uno.pe"]

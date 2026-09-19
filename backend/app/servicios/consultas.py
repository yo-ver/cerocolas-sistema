"""Consultas de cruces y calculo de indicadores sobre la base de datos."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import obtener_config
from app.models import Cruce, SalaEspera
from app.servicios import curvas
from app.servicios.estimadores import ContextoEstimacion, construir_estimador
from app.servicios.tiempo import a_local

config = obtener_config()


def obtener_sala(sesion: Session, codigo: str) -> SalaEspera | None:
    return sesion.scalar(select(SalaEspera).where(SalaEspera.codigo == codigo))


def cruces_de_fecha(
    sesion: Session, sala_id, fecha: date, hasta: datetime | None = None
) -> tuple[list[datetime], list[datetime]]:
    """Devuelve (entradas, salidas) de una fecha operativa, en hora local.

    Se filtra por `fecha_operativa`, que se calcula al momento de la ingesta.
    Es una columna denormalizada a proposito: evita recalcular el corte de
    jornada en cada consulta y permite indexar directamente por dia.
    """
    consulta = select(Cruce.direccion, Cruce.ocurrido_en).where(
        Cruce.sala_id == sala_id,
        Cruce.fecha_operativa == fecha,
    )
    if hasta is not None:
        consulta = consulta.where(Cruce.ocurrido_en <= hasta)

    entradas: list[datetime] = []
    salidas: list[datetime] = []
    for direccion, ocurrido_en in sesion.execute(consulta):
        momento = a_local(ocurrido_en)
        (entradas if direccion == "IN" else salidas).append(momento)

    entradas.sort()
    salidas.sort()
    return entradas, salidas


def fechas_con_datos(sesion: Session, sala_id, desde: date, hasta: date) -> list[date]:
    filas = sesion.execute(
        select(Cruce.fecha_operativa)
        .where(
            Cruce.sala_id == sala_id,
            Cruce.fecha_operativa >= desde,
            Cruce.fecha_operativa <= hasta,
        )
        .distinct()
        .order_by(Cruce.fecha_operativa)
    ).scalars()
    return list(filas)


def contexto_del_dia(
    sesion: Session, sala: SalaEspera, fecha: date, hasta: datetime | None = None
) -> ContextoEstimacion:
    entradas, salidas = cruces_de_fecha(sesion, sala.id, fecha, hasta)
    return ContextoEstimacion(
        entradas=entradas,
        salidas=salidas,
        factor_correccion=float(sala.ratio_acompanante or 0),
        espera_maxima_min=config.espera_maxima_min,
        metadatos={"sala": sala.codigo, "fecha": fecha.isoformat()},
    )


def resultado_del_dia(
    sesion: Session, sala: SalaEspera, fecha: date, hasta: datetime | None = None
) -> curvas.ResultadoCurvas:
    """Deriva las esperas de una jornada con el estimador que la sala tenga.

    Strategy: el algoritmo lo elige la configuracion de la sala, no este
    modulo. Cambiar de estimador es actualizar una columna, no editar codigo.
    """
    estimador = construir_estimador(getattr(sala, "estimador", None))
    return estimador.estimar(contexto_del_dia(sesion, sala, fecha, hasta))

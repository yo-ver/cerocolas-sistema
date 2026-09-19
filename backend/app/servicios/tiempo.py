"""Manejo de fechas y zonas horarias.

Regla del sistema: todo instante se transporta en ISO 8601 con desfase
explicito, se almacena en UTC y se presenta en la zona operativa. En la base
manual de KoboCollect las fechas no tenian zona horaria, lo que ya habia
generado ambiguedades.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import obtener_config

config = obtener_config()
ZONA = ZoneInfo(config.zona_horaria)


def ahora_utc() -> datetime:
    return datetime.now(UTC)


def a_utc(dt: datetime) -> datetime:
    """Normaliza a UTC. Un instante sin zona se interpreta como zona operativa."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZONA)
    return dt.astimezone(UTC)


def a_local(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(ZONA)


def fecha_operativa(dt: datetime) -> date:
    """Fecha de la jornada a la que pertenece un instante.

    Antes de la hora de corte el instante se atribuye al dia anterior. Esto
    importa porque la cola exterior de estos hospitales empieza de madrugada y
    un cruce a las 5:40 a.m. pertenece a la jornada de ese dia, no a la
    anterior; el corte a las 3 a.m. deja ese caso del lado correcto sin partir
    ninguna jornada real.
    """
    local = a_local(dt)
    if local.hour < config.hora_corte_operativa:
        local -= timedelta(days=1)
    return local.date()


def rango_de_fecha(f: date) -> tuple[datetime, datetime]:
    """Instantes UTC de inicio y fin de una fecha operativa."""
    inicio_local = datetime.combine(f, datetime.min.time(), tzinfo=ZONA) + timedelta(
        hours=config.hora_corte_operativa
    )
    return a_utc(inicio_local), a_utc(inicio_local + timedelta(days=1))

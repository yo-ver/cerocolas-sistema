"""Cola local persistente de cruces pendientes de envio.

El agente opera en la red de un hospital, que se cae. Sin esta cola, cada corte
de conectividad seria una perdida definitiva de mediciones justo en las horas
de mayor congestion. Con ella, el agente sigue midiendo y reenvia despues.

Se usa SQLite en archivo y no una cola en memoria porque el equipo de borde
tambien se apaga: un corte de energia no debe llevarse la jornada.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from agente.conteo import CruceDetectado

ESQUEMA = """
CREATE TABLE IF NOT EXISTS pendiente (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    linea         TEXT    NOT NULL,
    direccion     TEXT    NOT NULL CHECK (direccion IN ('IN', 'OUT')),
    ocurrido_en   TEXT    NOT NULL,
    confianza     REAL,
    track_id      INTEGER,
    enviado       INTEGER NOT NULL DEFAULT 0,
    intentos      INTEGER NOT NULL DEFAULT 0,
    creado_en     TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_pendiente_enviado ON pendiente (enviado, id);
"""


class ColaLocal:
    def __init__(self, ruta: str | Path):
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        with self._conexion() as conexion:
            conexion.executescript(ESQUEMA)

    @contextmanager
    def _conexion(self) -> Iterator[sqlite3.Connection]:
        conexion = sqlite3.connect(self.ruta, timeout=30)
        conexion.row_factory = sqlite3.Row
        try:
            # WAL permite que el hilo de captura escriba mientras el de envio
            # lee, sin bloquearse mutuamente.
            conexion.execute("PRAGMA journal_mode=WAL")
            yield conexion
            conexion.commit()
        finally:
            conexion.close()

    def encolar(self, cruces: list[CruceDetectado]) -> int:
        if not cruces:
            return 0
        with self._conexion() as conexion:
            conexion.executemany(
                "INSERT INTO pendiente (linea, direccion, ocurrido_en, confianza, track_id) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        c.linea,
                        c.direccion,
                        c.ocurrido_en.isoformat(),
                        round(c.confianza, 3),
                        c.track_id,
                    )
                    for c in cruces
                ],
            )
        return len(cruces)

    def tomar_lote(self, tamano: int = 200) -> list[dict]:
        """Devuelve los cruces mas antiguos sin enviar, sin marcarlos aun.

        No se marcan hasta confirmar el envio: si el proceso muere entre la
        lectura y la respuesta del servidor, el lote se reintenta. Un reenvio
        es inofensivo porque la API es idempotente; una perdida no lo es.
        """
        with self._conexion() as conexion:
            filas = conexion.execute(
                "SELECT id, linea, direccion, ocurrido_en, confianza, track_id "
                "FROM pendiente WHERE enviado = 0 ORDER BY id LIMIT ?",
                (tamano,),
            ).fetchall()
        return [dict(fila) for fila in filas]

    def confirmar(self, ids: list[int]) -> None:
        if not ids:
            return
        with self._conexion() as conexion:
            conexion.executemany(
                "UPDATE pendiente SET enviado = 1 WHERE id = ?", [(i,) for i in ids]
            )

    def registrar_intento_fallido(self, ids: list[int]) -> None:
        if not ids:
            return
        with self._conexion() as conexion:
            conexion.executemany(
                "UPDATE pendiente SET intentos = intentos + 1 WHERE id = ?",
                [(i,) for i in ids],
            )

    def pendientes(self) -> int:
        with self._conexion() as conexion:
            return conexion.execute("SELECT COUNT(*) FROM pendiente WHERE enviado = 0").fetchone()[
                0
            ]

    def purgar_enviados(self, dias: int = 7) -> int:
        """Elimina lo ya confirmado con antiguedad suficiente.

        Se conserva una semana en lugar de borrar de inmediato: si se detecta
        un problema de calibracion, permite reconstruir lo enviado sin volver
        al video, que ya no existe.
        """
        with self._conexion() as conexion:
            cursor = conexion.execute(
                "DELETE FROM pendiente WHERE enviado = 1 AND creado_en <= datetime('now', ?)",
                (f"-{dias} days",),
            )
            return cursor.rowcount

    def resumen(self) -> dict:
        with self._conexion() as conexion:
            fila = conexion.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN enviado = 0 THEN 1 ELSE 0 END) AS pendientes, "
                "MIN(CASE WHEN enviado = 0 THEN ocurrido_en END) AS mas_antiguo "
                "FROM pendiente"
            ).fetchone()
        return {
            "total": fila["total"] or 0,
            "pendientes": fila["pendientes"] or 0,
            "mas_antiguo": fila["mas_antiguo"],
        }


def antiguedad_minutos(iso: str | None, ahora: datetime) -> float | None:
    if not iso:
        return None
    return (ahora - datetime.fromisoformat(iso)).total_seconds() / 60

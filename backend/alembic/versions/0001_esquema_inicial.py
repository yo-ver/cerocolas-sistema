"""Esquema inicial y particionado de la tabla de cruces.

Revision ID: 0001
Create Date: 2026-08-15

El esquema se crea desde los modelos SQLAlchemy. Esta migracion agrega ademas
lo que es especifico de PostgreSQL y no se puede expresar en el ORM: el
particionado por rango de `cruce.ocurrido_en`.

Por que particionar: la tabla de cruces crece a razon de unas 8 000 filas por
sala y por dia, y todas las consultas del tablero son por rango de fechas. El
particionado mensual permite que PostgreSQL descarte particiones enteras en
lugar de recorrer el indice completo, y facilita archivar jornadas antiguas.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _es_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _es_postgres():
        # En SQLite (desarrollo y pruebas) el esquema lo crea
        # Base.metadata.create_all; no hay particionado que aplicar.
        return

    # Particiones mensuales para el periodo del piloto. La funcion de
    # mantenimiento crea las siguientes de forma automatica.
    op.execute("""
        CREATE OR REPLACE FUNCTION crear_particion_cruce(mes date)
        RETURNS void AS $$
        DECLARE
            nombre text := 'cruce_' || to_char(mes, 'YYYY_MM');
            inicio date := date_trunc('month', mes);
            fin    date := (date_trunc('month', mes) + interval '1 month')::date;
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = nombre) THEN
                EXECUTE format(
                    'CREATE TABLE %I PARTITION OF cruce FOR VALUES FROM (%L) TO (%L)',
                    nombre, inicio, fin
                );
            END IF;
        END;
        $$ LANGUAGE plpgsql;
    """)

    for desplazamiento in range(-2, 13):
        op.execute(
            f"SELECT crear_particion_cruce((date_trunc('month', now()) "
            f"+ interval '{desplazamiento} month')::date);"
        )

    # Indice de apoyo para la consulta mas frecuente del tablero:
    # cruces de una sala en una fecha operativa.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_cruce_sala_fecha_operativa
        ON cruce (sala_id, fecha_operativa, direccion);
    """)


def downgrade() -> None:
    if not _es_postgres():
        return
    op.execute("DROP INDEX IF EXISTS ix_cruce_sala_fecha_operativa;")
    op.execute("DROP FUNCTION IF EXISTS crear_particion_cruce(date);")

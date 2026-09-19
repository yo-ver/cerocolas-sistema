"""Modelos ORM. Implementan el diagrama entidad-relacion de la propuesta.

Principio de diseno: los tiempos de espera nunca se escriben a mano. Se derivan
siempre de los cruces. Por eso `cruce` es la unica tabla que recibe datos de
campo, y `estimacion_espera` e `indicador_ventana` son resultados recalculables.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# Enumeraciones del dominio -------------------------------------------------

TIPO_LINEA = Enum("ENTRADA", "SALIDA", name="tipo_linea")
DIRECCION = Enum("IN", "OUT", name="direccion_cruce")
ROL = Enum("ADMIN", "GESTOR", "VISOR", "AGENTE", name="rol_usuario")
CALIDAD = Enum("ESTIMADA", "SOSPECHOSA", "DESCARTADA", name="calidad_estimacion")


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


# --- Grupo 1: catalogo institucional ---------------------------------------


class Establecimiento(Base):
    __tablename__ = "establecimiento"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    # Codigo unico de RENIPRESS. Es la clave canonica: en la base manual el
    # mismo hospital aparecia escrito de varias formas distintas.
    codigo_unico: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(160))
    categoria: Mapped[str | None] = mapped_column(String(10))
    provincia: Mapped[str | None] = mapped_column(String(60))
    latitud: Mapped[float | None] = mapped_column(Numeric(9, 6))
    longitud: Mapped[float | None] = mapped_column(Numeric(9, 6))

    salas: Mapped[list[SalaEspera]] = relationship(back_populates="establecimiento")


class SalaEspera(Base):
    __tablename__ = "sala_espera"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    establecimiento_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("establecimiento.id", ondelete="RESTRICT")
    )
    codigo: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(120))
    aforo_referencial: Mapped[int | None] = mapped_column(Integer)
    hora_apertura: Mapped[time] = mapped_column(Time, default=time(6, 0))
    hora_cierre: Mapped[time] = mapped_column(Time, default=time(14, 0))
    # Ratio de acompanantes medido en calibracion. Corrige la curva de entradas:
    # ignorarlo infla la estimacion de espera de forma sustancial.
    ratio_acompanante: Mapped[float] = mapped_column(Numeric(4, 3), default=0)
    # Strategy: nombre del estimador de espera aplicado a esta sala. Permite
    # que dos salas con comportamientos distintos usen algoritmos distintos sin
    # bifurcaciones dentro del calculo.
    estimador: Mapped[str] = mapped_column(String(40), default="fifo_corregido")
    activo: Mapped[bool] = mapped_column(Boolean, default=True)

    establecimiento: Mapped[Establecimiento] = relationship(back_populates="salas")
    camaras: Mapped[list[Camara]] = relationship(back_populates="sala")


class Usuario(Base):
    __tablename__ = "usuario"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    establecimiento_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("establecimiento.id", ondelete="RESTRICT")
    )
    email: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    hash_password: Mapped[str] = mapped_column(String(255))
    nombre: Mapped[str | None] = mapped_column(String(120))
    rol: Mapped[str] = mapped_column(ROL, default="VISOR")
    activo: Mapped[bool] = mapped_column(Boolean, default=True)


# --- Grupo 2: configuracion de captura -------------------------------------


class Camara(Base):
    __tablename__ = "camara"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    sala_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sala_espera.id", ondelete="CASCADE"))
    codigo: Mapped[str] = mapped_column(String(40), unique=True)
    url_rtsp: Mapped[str] = mapped_column(Text)
    resolucion: Mapped[str | None] = mapped_column(String(20))
    fps_objetivo: Mapped[int] = mapped_column(Integer, default=10)
    estado: Mapped[str] = mapped_column(String(20), default="ACTIVA")

    sala: Mapped[SalaEspera] = relationship(back_populates="camaras")
    lineas: Mapped[list[LineaVirtual]] = relationship(back_populates="camara")


class LineaVirtual(Base):
    __tablename__ = "linea_virtual"
    __table_args__ = (UniqueConstraint("sala_id", "nombre", name="uq_linea_sala_nombre"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    camara_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("camara.id", ondelete="CASCADE"))
    # Se desnormaliza sala_id a proposito: el motor de curvas consulta siempre
    # por sala y este atajo evita un join en la ruta caliente.
    sala_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sala_espera.id", ondelete="CASCADE"))
    tipo: Mapped[str] = mapped_column(TIPO_LINEA)
    nombre: Mapped[str] = mapped_column(String(60))
    consultorio_ref: Mapped[str | None] = mapped_column(String(40))
    # Poligono o segmento en coordenadas de imagen: {"x1":..,"y1":..,"x2":..,"y2":..}
    geometria: Mapped[dict] = mapped_column(JSON)
    sentido_positivo: Mapped[str] = mapped_column(String(10), default="IN")

    camara: Mapped[Camara] = relationship(back_populates="lineas")


class Agente(Base):
    __tablename__ = "agente"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    establecimiento_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("establecimiento.id", ondelete="RESTRICT")
    )
    hostname: Mapped[str] = mapped_column(String(80), unique=True)
    version_modelo: Mapped[str | None] = mapped_column(String(40))
    ultimo_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    estado: Mapped[str] = mapped_column(String(20), default="DESCONECTADO")


# --- Grupo 3: eventos crudos -----------------------------------------------


class LoteIngesta(Base):
    """Un envio de eventos desde un agente.

    La clave de idempotencia es lo que permite al agente reintentar sin miedo
    tras una caida de red: un segundo envio con la misma clave devuelve el
    resultado del primero en lugar de duplicar los cruces.
    """

    __tablename__ = "lote_ingesta"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    agente_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agente.id"))
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    recibido_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    n_eventos: Mapped[int] = mapped_column(Integer, default=0)
    n_aceptados: Mapped[int] = mapped_column(Integer, default=0)
    n_rechazados: Mapped[int] = mapped_column(Integer, default=0)
    estado: Mapped[str] = mapped_column(String(20), default="PROCESADO")


class Cruce(Base):
    """Evento atomico: alguien cruzo una linea en un sentido y en un instante.

    Es el unico hecho que el sistema observa. Nunca contiene identidad: no hay
    forma de asociar un cruce con una persona concreta.

    En PostgreSQL esta tabla se particiona por rango sobre `ocurrido_en`; ver
    alembic/versions/0002_particionado.py.
    """

    __tablename__ = "cruce"
    __table_args__ = (
        Index("ix_cruce_sala_tiempo", "sala_id", "ocurrido_en"),
        Index("ix_cruce_linea_tiempo", "linea_id", "ocurrido_en"),
        CheckConstraint(
            "confianza IS NULL OR (confianza >= 0 AND confianza <= 1)", name="ck_cruce_confianza"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    linea_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("linea_virtual.id"))
    sala_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sala_espera.id"))
    lote_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lote_ingesta.id"))
    direccion: Mapped[str] = mapped_column(DIRECCION)
    # Instante real del cruce, reportado por el agente.
    ocurrido_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # Instante en que el servidor lo recibio. La diferencia con el anterior
    # revela desfases de reloj y cortes de red en el equipo de borde.
    recibido_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fecha_operativa: Mapped[date] = mapped_column(Date, index=True)
    confianza: Mapped[float | None] = mapped_column(Numeric(4, 3))
    track_id_local: Mapped[int | None] = mapped_column(Integer)


class Aforo(Base):
    """Conteo directo de ocupacion.

    Sirve para corregir la deriva del calculo acumulado: si la sala no tiene
    una entrada unica bien definida, N_in - N_out se desajusta a lo largo del
    dia y este conteo permite reanclarlo.
    """

    __tablename__ = "aforo"
    __table_args__ = (Index("ix_aforo_sala_tiempo", "sala_id", "ocurrido_en"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sala_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sala_espera.id"))
    ocurrido_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    conteo: Mapped[int] = mapped_column(Integer)
    metodo: Mapped[str] = mapped_column(String(20), default="VISION")
    confianza: Mapped[float | None] = mapped_column(Numeric(4, 3))


# --- Grupo 4: resultados analiticos ----------------------------------------


class EstimacionEspera(Base):
    __tablename__ = "estimacion_espera"
    __table_args__ = (
        UniqueConstraint("sala_id", "fecha_operativa", "secuencia_k", name="uq_estimacion_k"),
        CheckConstraint("espera_min >= 0", name="ck_espera_no_negativa"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sala_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sala_espera.id"))
    fecha_operativa: Mapped[date] = mapped_column(Date, index=True)
    secuencia_k: Mapped[int] = mapped_column(Integer)
    t_entrada: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    t_salida: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    espera_min: Mapped[float] = mapped_column(Numeric(8, 2))
    calidad: Mapped[str] = mapped_column(CALIDAD, default="ESTIMADA")


class IndicadorVentana(Base):
    __tablename__ = "indicador_ventana"
    __table_args__ = (UniqueConstraint("sala_id", "inicio", "fin", name="uq_indicador_ventana"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sala_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sala_espera.id"))
    fecha_operativa: Mapped[date] = mapped_column(Date, index=True)
    inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fin: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    n_in: Mapped[int] = mapped_column(Integer, default=0)
    n_out: Mapped[int] = mapped_column(Integer, default=0)
    ocupacion_fin: Mapped[int] = mapped_column(Integer, default=0)
    espera_p50: Mapped[float | None] = mapped_column(Numeric(8, 2))
    espera_p90: Mapped[float | None] = mapped_column(Numeric(8, 2))


class Calibracion(Base):
    """Observacion manual con cronometro, contra la que se mide el error."""

    __tablename__ = "calibracion"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sala_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sala_espera.id"))
    observador: Mapped[str] = mapped_column(String(80))
    t_entrada_obs: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    t_salida_obs: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    espera_obs_min: Mapped[float] = mapped_column(Numeric(8, 2))
    espera_sistema_min: Mapped[float | None] = mapped_column(Numeric(8, 2))
    error_min: Mapped[float | None] = mapped_column(Numeric(8, 2))
    con_acompanante: Mapped[bool | None] = mapped_column(Boolean)
    notas: Mapped[str | None] = mapped_column(Text)

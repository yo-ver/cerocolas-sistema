"""Esquemas Pydantic. Definen el contrato publico de la API.

FastAPI genera la especificacion OpenAPI a partir de estas clases, de modo que
la documentacion de la interfaz no requiere trabajo adicional.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --- autenticacion ---------------------------------------------------------


class SolicitudToken(BaseModel):
    # Deliberadamente `str` y no `EmailStr`: el login es una busqueda, no un
    # alta. Validar el formato aqui rechazaria dominios internos legitimos
    # (p. ej. .local) y devolveria 422 en lugar de 401, revelando al atacante
    # que la direccion no existe siquiera como cadena valida.
    email: str
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    rol: str
    expira_en: datetime


# --- ingesta de cruces -----------------------------------------------------


class CruceEntrada(BaseModel):
    """Un cruce reportado por el agente de vision."""

    linea: str = Field(description="Nombre de la linea virtual, p. ej. ENTRADA_SALA")
    direccion: Literal["IN", "OUT"]
    ocurrido_en: datetime = Field(description="ISO 8601 con desfase horario explicito")
    confianza: float | None = Field(default=None, ge=0, le=1)
    track_id_local: int | None = Field(
        default=None,
        description="Identificador de seguimiento local del agente. No identifica "
        "a ninguna persona: se reinicia con cada arranque y solo sirve "
        "para depurar conteos duplicados.",
    )

    @field_validator("ocurrido_en")
    @classmethod
    def exigir_zona_horaria(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("ocurrido_en debe incluir desfase horario explicito (ISO 8601)")
        return v


class LoteCruces(BaseModel):
    sala_id: str = Field(description="Codigo de la sala, p. ej. LOR-CE-01")
    agente: str | None = Field(default=None, description="Hostname del agente emisor")
    cruces: list[CruceEntrada] = Field(min_length=1, max_length=5000)


class RechazoCruce(BaseModel):
    indice: int
    motivo: str


class RespuestaLote(BaseModel):
    lote_id: uuid.UUID
    recibidos: int
    aceptados: int
    rechazados: int
    duplicado: bool = Field(
        default=False,
        description="True si la clave de idempotencia ya habia sido procesada; "
        "en ese caso no se insertaron cruces nuevos.",
    )
    detalles: list[RechazoCruce] = []


# --- aforo -----------------------------------------------------------------


class AforoEntrada(BaseModel):
    sala_id: str
    ocurrido_en: datetime
    conteo: int = Field(ge=0)
    metodo: str = "VISION"
    confianza: float | None = Field(default=None, ge=0, le=1)


# --- consultas -------------------------------------------------------------


class EstadoSala(BaseModel):
    sala_id: str
    nombre: str
    establecimiento: str
    fecha_operativa: date
    momento: datetime
    ocupacion: int
    n_entradas: int
    n_salidas: int
    espera_estimada_min: float | None
    espera_p50_min: float | None
    espera_p90_min: float | None
    ratio_acompanante: float
    muestras: int


class PuntoCurva(BaseModel):
    t: datetime
    n_in: int
    n_out: int
    ocupacion: int


class RespuestaCurvas(BaseModel):
    sala_id: str
    fecha_operativa: date
    paso_min: int
    puntos: list[PuntoCurva]


class IndicadorDia(BaseModel):
    sala_id: str
    fecha_operativa: date
    n_pacientes: int
    espera_p50_min: float | None
    espera_p90_min: float | None
    espera_promedio_min: float | None
    ocupacion_maxima: int
    descartados: int


class RespuestaIndicadores(BaseModel):
    desde: date
    hasta: date
    filas: list[IndicadorDia]
    resumen_p50_min: float | None
    resumen_p90_min: float | None


# --- calibracion -----------------------------------------------------------


class CalibracionEntrada(BaseModel):
    sala_id: str
    observador: str
    t_entrada_obs: datetime
    t_salida_obs: datetime
    con_acompanante: bool | None = None
    notas: str | None = None


class CalibracionSalida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    observador: str
    espera_obs_min: float
    espera_sistema_min: float | None
    error_min: float | None


class ResumenCalibracion(BaseModel):
    sala_id: str
    n_observaciones: int
    error_absoluto_medio_min: float | None
    sesgo_min: float | None
    ratio_acompanante_observado: float | None


# --- catalogos -------------------------------------------------------------


class SalaSalida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    codigo: str
    nombre: str
    establecimiento: str
    aforo_referencial: int | None
    ratio_acompanante: float
    estimador: str
    activo: bool


class FilaComparacion(BaseModel):
    estimador: str
    descripcion: str
    muestras: int
    descartados: int
    espera_p50_min: float | None
    espera_p90_min: float | None


class RespuestaComparacion(BaseModel):
    sala_id: str
    fecha_operativa: date
    estimador_activo: str
    filas: list[FilaComparacion]

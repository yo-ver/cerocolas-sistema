"""Configuracion de la aplicacion, leida desde variables de entorno."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- base de datos -----------------------------------------------------
    # En produccion: postgresql+psycopg://usuario:clave@host:5432/cerocolas
    database_url: str = "sqlite+pysqlite:///./cerocolas.db"

    # --- seguridad ---------------------------------------------------------
    jwt_secreto: str = "cambiar-en-produccion"
    jwt_algoritmo: str = "HS256"
    jwt_minutos_expiracion: int = 720

    # --- parametros del dominio -------------------------------------------
    # Zona horaria operativa. Todo se almacena en UTC y se presenta en esta.
    zona_horaria: str = "America/Lima"

    # Hora a partir de la cual empieza una nueva "fecha operativa". Antes de
    # esta hora los cruces se atribuyen al dia anterior. Se usa 3 a.m. porque
    # las colas de madrugada empiezan alrededor de las 5 y ningun servicio
    # opera de madrugada.
    hora_corte_operativa: int = 3

    # Espera maxima plausible en minutos. Por encima de este valor el registro
    # se marca como SOSPECHOSO en lugar de descartarse. La base manual de
    # KoboCollect contenia esperas de 10 110 minutos, claramente erroneas.
    espera_maxima_min: float = 480.0

    # Ratio de acompanantes por paciente usado por defecto para corregir la
    # curva de entradas. Se sobrescribe por sala con el valor medido en la
    # calibracion manual.
    ratio_acompanante_defecto: float = 0.0

    # --- operacion ---------------------------------------------------------
    entorno: str = "desarrollo"
    cors_origenes: str = "http://localhost:5173"

    @property
    def lista_cors(self) -> list[str]:
        return [o.strip() for o in self.cors_origenes.split(",") if o.strip()]

    @property
    def es_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")


@lru_cache
def obtener_config() -> Config:
    return Config()

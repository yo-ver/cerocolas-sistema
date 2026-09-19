"""Conexion a la base de datos y sesion de trabajo."""

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import obtener_config

config = obtener_config()

_kwargs: dict = {"pool_pre_ping": True}
# Esta rama la elige el entorno de despliegue, no la logica del programa: en
# desarrollo y en las pruebas la URL apunta a SQLite, en produccion a
# PostgreSQL. Ejercitar ambos caminos exigiria reimportar el modulo con otra
# configuracion, lo que aportaria menos de lo que confundiria.
if config.database_url.startswith("sqlite"):  # pragma: no cover
    # check_same_thread es necesario para que TestClient use la misma conexion.
    _kwargs["connect_args"] = {"check_same_thread": False}

motor = create_engine(config.database_url, **_kwargs)

if config.database_url.startswith("sqlite"):

    @event.listens_for(motor, "connect")
    def _activar_claves_foraneas(conexion, _registro):  # pragma: no cover
        cursor = conexion.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SesionLocal = sessionmaker(bind=motor, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def obtener_sesion() -> Iterator[Session]:
    """Dependencia de FastAPI que entrega una sesion por peticion."""
    sesion = SesionLocal()
    try:
        yield sesion
    finally:
        sesion.close()

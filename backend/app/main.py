"""Punto de entrada de la API.

Sistema de medicion del tiempo de espera en sala de espera de consultorios
externos. Hospital Regional del Cusco y Hospital Antonio Lorena.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import obtener_config
from app.routers import auth, calibracion, indicadores, ingesta

config = obtener_config()

DESCRIPCION = """
API del sistema de medicion automatizada del tiempo de espera, en el marco del
Plan Cero Colas (RM N.° 811-2018/MINSA).

**Como funciona.** Los agentes de vision instalados en cada hospital detectan
cruces de personas en lineas virtuales y los envian a `/v1/cruces:lote`. El
servidor reconstruye las curvas de flujo acumulado y deriva la ocupacion y el
tiempo de espera.

**Que no hace.** No identifica personas, no almacena imagenes y no realiza
reconocimiento facial ni re-identificacion. Un cruce solo registra que alguien
atraveso una linea en un sentido y en un instante.
"""

app = FastAPI(
    title="Cero Colas — Medicion de tiempo de espera",
    version="0.1.0",
    description=DESCRIPCION,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.lista_cors,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(ingesta.router)
app.include_router(indicadores.router)
app.include_router(calibracion.router)


@app.get("/salud", tags=["operacion"], summary="Verificacion de disponibilidad")
def salud() -> dict:
    return {"estado": "ok", "entorno": config.entorno, "version": app.version}

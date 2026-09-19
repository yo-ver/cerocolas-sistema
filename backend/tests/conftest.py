"""Fixtures de prueba. Se usa SQLite en archivo temporal para no requerir
PostgreSQL en la maquina de desarrollo ni en integracion continua.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# La configuracion debe fijarse antes de importar la aplicacion.
_TMP = Path(tempfile.mkdtemp())
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_TMP / 'prueba.db'}"
os.environ["JWT_SECRETO"] = "secreto-de-prueba"

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import Base, SesionLocal, motor
from app.main import app
from app.models import (
    Camara,
    Establecimiento,
    LineaVirtual,
    SalaEspera,
    Usuario,
)
from app.security import hashear_password

CLAVE = "clave-de-prueba"


@pytest.fixture(scope="session", autouse=True)
def base_de_datos():
    Base.metadata.create_all(motor)
    sesion = SesionLocal()

    est = Establecimiento(
        codigo_unico="TEST-001", nombre="Hospital de prueba", categoria="III-1", provincia="CUSCO"
    )
    sesion.add(est)
    sesion.flush()

    sala = SalaEspera(
        establecimiento_id=est.id,
        codigo="TEST-CE-01",
        nombre="Sala de prueba",
        aforo_referencial=100,
        ratio_acompanante=0,
    )
    sesion.add(sala)
    sesion.flush()

    cam = Camara(sala_id=sala.id, codigo="TEST-CAM01", url_rtsp="rtsp://x/1")
    sesion.add(cam)
    sesion.flush()

    sesion.add(
        LineaVirtual(
            camara_id=cam.id,
            sala_id=sala.id,
            tipo="ENTRADA",
            nombre="ENTRADA_SALA",
            geometria={"x1": 0, "y1": 0, "x2": 10, "y2": 0},
        )
    )
    for n in (1, 2):
        sesion.add(
            LineaVirtual(
                camara_id=cam.id,
                sala_id=sala.id,
                tipo="SALIDA",
                nombre=f"PUERTA_CONS_{n:02d}",
                consultorio_ref=f"CONS-{n:02d}",
                geometria={"x1": 0, "y1": 5, "x2": 10, "y2": 5},
            )
        )

    for email, rol in (
        ("agente@test.local", "AGENTE"),
        ("visor@test.local", "VISOR"),
        ("gestor@test.local", "GESTOR"),
    ):
        sesion.add(
            Usuario(
                establecimiento_id=est.id,
                email=email,
                hash_password=hashear_password(CLAVE),
                rol=rol,
            )
        )

    sesion.commit()
    sesion.close()
    yield
    Base.metadata.drop_all(motor)


@pytest.fixture
def cliente():
    with TestClient(app) as c:
        yield c


def _token(cliente: TestClient, email: str) -> str:
    respuesta = cliente.post("/v1/auth/token", json={"email": email, "password": CLAVE})
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()["access_token"]


@pytest.fixture
def cabecera_agente(cliente):
    return {"Authorization": f"Bearer {_token(cliente, 'agente@test.local')}"}


@pytest.fixture
def cabecera_visor(cliente):
    return {"Authorization": f"Bearer {_token(cliente, 'visor@test.local')}"}


@pytest.fixture
def cabecera_gestor(cliente):
    return {"Authorization": f"Bearer {_token(cliente, 'gestor@test.local')}"}


@pytest.fixture
def sala_id():
    sesion = SesionLocal()
    try:
        return sesion.scalar(select(SalaEspera.id).where(SalaEspera.codigo == "TEST-CE-01"))
    finally:
        sesion.close()


def pytest_collection_modifyitems(items):
    """Asigna el marcador segun la carpeta de la prueba.

    Evita tener que recordar el decorador en cada funcion: la ubicacion del
    archivo ya expresa el nivel, y duplicar esa informacion a mano garantiza
    que tarde o temprano diverjan.
    """
    for item in items:
        ruta = str(item.fspath)
        if "unitarias" in ruta:
            item.add_marker("unitaria")
        elif "integracion" in ruta:
            item.add_marker("integracion")

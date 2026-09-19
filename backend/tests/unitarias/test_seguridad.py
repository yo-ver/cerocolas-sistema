"""Pruebas de las primitivas de autenticacion.

Se verifican en aislamiento porque su comportamiento ante entradas corruptas
—un hash truncado, un token manipulado— es precisamente el camino que nunca se
ejercita durante el uso normal del sistema y donde se concentran los defectos
con consecuencias de seguridad.
"""

from __future__ import annotations

from datetime import UTC, timedelta

import pytest
from fastapi import HTTPException
from jose import jwt

from app.config import obtener_config
from app.security import (
    _decodificar,
    crear_token,
    hashear_password,
    verificar_password,
)

config = obtener_config()


def test_el_hash_no_contiene_la_clave():
    almacenado = hashear_password("clave-secreta-123")
    assert "clave-secreta-123" not in almacenado


def test_dos_hashes_de_la_misma_clave_difieren():
    """Cada hash lleva su propia sal.

    Sin sal por registro, dos usuarios con la misma clave producirian el mismo
    hash y una filtracion revelaria esa coincidencia.
    """
    assert hashear_password("misma") != hashear_password("misma")


def test_verifica_la_clave_correcta():
    assert verificar_password("correcta", hashear_password("correcta"))


def test_rechaza_la_clave_incorrecta():
    assert not verificar_password("incorrecta", hashear_password("correcta"))


@pytest.mark.parametrize(
    "almacenado",
    [
        "",
        "sin-formato",
        "pbkdf2_sha256$mal",
        "pbkdf2_sha256$no-es-numero$aabb$ccdd",
        None,
    ],
)
def test_un_hash_corrupto_no_valida_y_no_revienta(almacenado):
    """Un registro dañado debe negar el acceso, no derribar el proceso.

    Si la excepcion se propagara, el endpoint devolveria 500 en lugar de 401 y
    revelaria que esa cuenta existe pero tiene el hash mal escrito.
    """
    assert not verificar_password("cualquiera", almacenado)


def test_el_token_transporta_sujeto_rol_y_ambito():
    token, expira = crear_token("visor@ejemplo.pe", "VISOR", "est-001")
    carga = _decodificar(token)

    assert carga["sub"] == "visor@ejemplo.pe"
    assert carga["rol"] == "VISOR"
    assert carga["est"] == "est-001"
    assert expira > __import__("datetime").datetime.now(__import__("datetime").UTC)


def test_un_token_manipulado_se_rechaza():
    token, _ = crear_token("visor@ejemplo.pe", "VISOR", None)
    manipulado = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")

    with pytest.raises(HTTPException) as excepcion:
        _decodificar(manipulado)
    assert excepcion.value.status_code == 401


def test_un_token_firmado_con_otro_secreto_se_rechaza():
    """Comprueba que la firma se valida y no solo se decodifica."""
    ajeno = jwt.encode({"sub": "intruso"}, "otro-secreto", algorithm="HS256")

    with pytest.raises(HTTPException) as excepcion:
        _decodificar(ajeno)
    assert excepcion.value.status_code == 401


def test_un_token_expirado_se_rechaza():
    from datetime import datetime

    vencido = jwt.encode(
        {
            "sub": "visor@ejemplo.pe",
            "rol": "VISOR",
            "exp": datetime.now(UTC) - timedelta(minutes=1),
        },
        config.jwt_secreto,
        algorithm=config.jwt_algoritmo,
    )

    with pytest.raises(HTTPException) as excepcion:
        _decodificar(vencido)
    assert excepcion.value.status_code == 401

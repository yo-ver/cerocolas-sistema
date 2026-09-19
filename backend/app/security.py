"""Autenticacion y autorizacion.

Dos clases de credencial con permisos deliberadamente asimetricos:
  - AGENTE: solo escribe eventos. No puede leer indicadores. Si el equipo de
    borde de un hospital fuera comprometido, no expondria informacion.
  - VISOR / GESTOR / ADMIN: leen indicadores del ambito de su establecimiento.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import obtener_config
from app.db import obtener_sesion
from app.models import Usuario

config = obtener_config()
esquema_oauth = OAuth2PasswordBearer(tokenUrl="/v1/auth/token", auto_error=False)

_ITERACIONES = 260_000


def hashear_password(password: str) -> str:
    """PBKDF2-HMAC-SHA256. Sin dependencias binarias, adecuado para el piloto."""
    sal = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), sal, _ITERACIONES)
    return f"pbkdf2_sha256${_ITERACIONES}${sal.hex()}${dk.hex()}"


def verificar_password(password: str, almacenado: str) -> bool:
    try:
        _, iteraciones, sal_hex, dk_hex = almacenado.split("$")
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(sal_hex), int(iteraciones)
        )
        return hmac.compare_digest(dk.hex(), dk_hex)
    except (ValueError, AttributeError):
        return False


def crear_token(sujeto: str, rol: str, establecimiento: str | None) -> tuple[str, datetime]:
    expira = datetime.now(UTC) + timedelta(minutes=config.jwt_minutos_expiracion)
    carga = {
        "sub": sujeto,
        "rol": rol,
        "est": establecimiento,
        "exp": expira,
    }
    token = jwt.encode(carga, config.jwt_secreto, algorithm=config.jwt_algoritmo)
    return token, expira


def _decodificar(token: str) -> dict:
    try:
        return jwt.decode(token, config.jwt_secreto, algorithms=[config.jwt_algoritmo])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credencial invalida o expirada",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def usuario_actual(
    token: Annotated[str | None, Depends(esquema_oauth)],
    sesion: Annotated[Session, Depends(obtener_sesion)],
) -> Usuario:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Se requiere autenticacion",
            headers={"WWW-Authenticate": "Bearer"},
        )
    carga = _decodificar(token)
    usuario = sesion.scalar(select(Usuario).where(Usuario.email == carga.get("sub")))
    if usuario is None or not usuario.activo:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario inexistente o inactivo"
        )
    return usuario


def exigir_roles(*roles: str):
    """Genera una dependencia que restringe el acceso a los roles indicados."""

    def dependencia(usuario: Annotated[Usuario, Depends(usuario_actual)]) -> Usuario:
        if usuario.rol not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"El rol {usuario.rol} no tiene permiso para esta operacion",
            )
        return usuario

    return dependencia


# Atajos usados por los routers
solo_agente = exigir_roles("AGENTE", "ADMIN")
solo_lectura = exigir_roles("VISOR", "GESTOR", "ADMIN")
solo_gestion = exigir_roles("GESTOR", "ADMIN")

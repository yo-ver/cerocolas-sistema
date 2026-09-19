"""Emision de tokens de acceso."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import obtener_sesion
from app.models import Usuario
from app.schemas import SolicitudToken, Token
from app.security import crear_token, verificar_password

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/token", response_model=Token, summary="Obtener token de acceso")
def emitir_token(
    cuerpo: SolicitudToken,
    sesion: Annotated[Session, Depends(obtener_sesion)],
) -> Token:
    usuario = sesion.scalar(select(Usuario).where(Usuario.email == cuerpo.email))

    # Mensaje unico para credencial inexistente o clave incorrecta: distinguir
    # ambos casos permitiria enumerar cuentas validas.
    if (
        usuario is None
        or not usuario.activo
        or not verificar_password(cuerpo.password, usuario.hash_password)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales invalidas",
            headers={"WWW-Authenticate": "Bearer"},
        )

    establecimiento = str(usuario.establecimiento_id) if usuario.establecimiento_id else None
    token, expira = crear_token(usuario.email, usuario.rol, establecimiento)
    return Token(access_token=token, rol=usuario.rol, expira_en=expira)

"""Ingesta de eventos desde los agentes de vision.

Estos son los unicos endpoints de escritura de datos de campo. Su requisito mas
importante no es el rendimiento sino la idempotencia: el agente trabaja con red
inestable y reintenta, y un reintento no puede producir cruces duplicados
porque falsearia las curvas acumuladas.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import obtener_config
from app.db import obtener_sesion
from app.models import Aforo, Agente, Cruce, LineaVirtual, LoteIngesta, Usuario
from app.schemas import AforoEntrada, LoteCruces, RechazoCruce, RespuestaLote
from app.security import solo_agente
from app.servicios.consultas import obtener_sala
from app.servicios.eventos import EventoLoteIngerido, publicador
from app.servicios.tiempo import a_utc, ahora_utc, fecha_operativa
from app.servicios.validacion import ContextoCruce, construir_cadena

config = obtener_config()
router = APIRouter(prefix="/v1", tags=["ingesta"])


@router.post(
    "/cruces:lote",
    response_model=RespuestaLote,
    status_code=status.HTTP_201_CREATED,
    summary="Ingesta por lotes de cruces detectados",
)
def ingestar_cruces(
    cuerpo: LoteCruces,
    sesion: Annotated[Session, Depends(obtener_sesion)],
    _usuario: Annotated[Usuario, Depends(solo_agente)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RespuestaLote:
    if not idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Se requiere la cabecera Idempotency-Key",
        )

    # Si la clave ya fue procesada devolvemos el resultado anterior sin insertar
    # nada. Es lo que permite al agente reintentar sin verificar antes.
    previo = sesion.scalar(
        select(LoteIngesta).where(LoteIngesta.idempotency_key == idempotency_key)
    )
    if previo:
        return RespuestaLote(
            lote_id=previo.id,
            recibidos=previo.n_eventos,
            aceptados=previo.n_aceptados,
            rechazados=previo.n_rechazados,
            duplicado=True,
        )

    sala = obtener_sala(sesion, cuerpo.sala_id)
    if sala is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Sala desconocida: {cuerpo.sala_id}")

    lineas = {
        linea.nombre: linea
        for linea in sesion.scalars(select(LineaVirtual).where(LineaVirtual.sala_id == sala.id))
    }

    agente = None
    if cuerpo.agente:
        agente = sesion.scalar(select(Agente).where(Agente.hostname == cuerpo.agente))
        if agente:
            agente.ultimo_heartbeat = ahora_utc()
            agente.estado = "ACTIVO"

    recibido_en = ahora_utc()
    lote = LoteIngesta(
        id=uuid.uuid4(),
        agente_id=agente.id if agente else None,
        idempotency_key=idempotency_key,
        recibido_en=recibido_en,
        n_eventos=len(cuerpo.cruces),
    )
    sesion.add(lote)
    sesion.flush()

    aceptados = 0
    rechazos: list[RechazoCruce] = []

    # Chain of Responsibility: el endpoint ya no sabe que reglas existen ni en
    # que orden se aplican. Solo recorre la cadena que la sala tenga
    # configurada, lo que permite anadir una regla sin tocar este codigo.
    cadena = construir_cadena()

    for i, evento in enumerate(cuerpo.cruces):
        linea = lineas.get(evento.linea)
        momento = a_utc(evento.ocurrido_en)

        rechazo = cadena.validar(
            ContextoCruce(
                indice=i,
                linea_nombre=evento.linea,
                direccion=evento.direccion,
                ocurrido_en=momento,
                confianza=evento.confianza,
                linea=linea,
                recibido_en=recibido_en,
                hora_apertura=sala.hora_apertura,
                hora_cierre=sala.hora_cierre,
            )
        )

        if rechazo is not None:
            rechazos.append(RechazoCruce(indice=rechazo.indice, motivo=rechazo.motivo))
            continue

        sesion.add(
            Cruce(
                linea_id=linea.id,
                sala_id=sala.id,
                lote_id=lote.id,
                direccion=evento.direccion,
                ocurrido_en=momento,
                recibido_en=recibido_en,
                fecha_operativa=fecha_operativa(momento),
                confianza=evento.confianza,
                track_id_local=evento.track_id_local,
            )
        )
        aceptados += 1

    lote.n_aceptados = aceptados
    lote.n_rechazados = len(rechazos)
    lote.estado = "PROCESADO" if not rechazos else "PARCIAL"
    sesion.commit()

    # Observer: se publica despues de confirmar la transaccion. Notificar antes
    # anunciaria cruces que todavia podrian perderse si el commit fallara.
    publicador.publicar(
        EventoLoteIngerido(
            sala_codigo=sala.codigo,
            lote_id=str(lote.id),
            agente=cuerpo.agente,
            recibidos=len(cuerpo.cruces),
            aceptados=aceptados,
            rechazados=len(rechazos),
            duplicado=False,
            momento=recibido_en,
            motivos=[r.motivo for r in rechazos[:5]],
        )
    )

    return RespuestaLote(
        lote_id=lote.id,
        recibidos=len(cuerpo.cruces),
        aceptados=aceptados,
        rechazados=len(rechazos),
        detalles=rechazos[:50],
    )


@router.post("/aforos", status_code=status.HTTP_201_CREATED, summary="Conteo directo de ocupacion")
def registrar_aforo(
    cuerpo: AforoEntrada,
    sesion: Annotated[Session, Depends(obtener_sesion)],
    _usuario: Annotated[Usuario, Depends(solo_agente)],
) -> dict:
    sala = obtener_sala(sesion, cuerpo.sala_id)
    if sala is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Sala desconocida: {cuerpo.sala_id}")

    sesion.add(
        Aforo(
            sala_id=sala.id,
            ocurrido_en=a_utc(cuerpo.ocurrido_en),
            conteo=cuerpo.conteo,
            metodo=cuerpo.metodo,
            confianza=cuerpo.confianza,
        )
    )
    sesion.commit()
    return {"registrado": True}

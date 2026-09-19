"""Calibracion contra observacion manual.

Es el endpoint que convierte al sistema en un instrumento de medicion en lugar
de un generador de numeros. Cada observacion con cronometro se compara con lo
que el sistema estimo para esa misma persona, y de ahi salen el error absoluto
medio y el sesgo que se reportan en el informe de validacion.

De aqui sale ademas el ratio de acompanantes por sala, que es el parametro que
mas afecta la exactitud del estimador.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import obtener_sesion
from app.models import Calibracion, Usuario
from app.schemas import (
    CalibracionEntrada,
    CalibracionSalida,
    ResumenCalibracion,
)
from app.security import solo_gestion, solo_lectura
from app.servicios.consultas import obtener_sala, resultado_del_dia
from app.servicios.tiempo import a_local, a_utc, fecha_operativa

router = APIRouter(prefix="/v1", tags=["calibracion"])


@router.post(
    "/calibraciones",
    response_model=CalibracionSalida,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar una observacion manual con cronometro",
)
def registrar_calibracion(
    cuerpo: CalibracionEntrada,
    sesion: Annotated[Session, Depends(obtener_sesion)],
    _usuario: Annotated[Usuario, Depends(solo_gestion)],
) -> CalibracionSalida:
    sala = obtener_sala(sesion, cuerpo.sala_id)
    if sala is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Sala desconocida: {cuerpo.sala_id}")

    t_entrada = a_utc(cuerpo.t_entrada_obs)
    t_salida = a_utc(cuerpo.t_salida_obs)
    if t_salida <= t_entrada:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "La hora de salida debe ser posterior a la de entrada",
        )

    espera_obs = (t_salida - t_entrada).total_seconds() / 60

    # Se busca la estimacion del sistema para el paciente que salio en el
    # instante mas cercano al observado.
    f = fecha_operativa(t_salida)
    resultado = resultado_del_dia(sesion, sala, f)
    salida_local = a_local(t_salida)

    espera_sistema = None
    if resultado.pares:
        _, _, _, espera_sistema = min(
            resultado.pares, key=lambda par: abs((par[2] - salida_local).total_seconds())
        )

    registro = Calibracion(
        sala_id=sala.id,
        observador=cuerpo.observador,
        t_entrada_obs=t_entrada,
        t_salida_obs=t_salida,
        espera_obs_min=round(espera_obs, 2),
        espera_sistema_min=round(espera_sistema, 2) if espera_sistema is not None else None,
        error_min=round(espera_sistema - espera_obs, 2) if espera_sistema is not None else None,
        con_acompanante=cuerpo.con_acompanante,
        notas=cuerpo.notas,
    )
    sesion.add(registro)
    sesion.commit()
    sesion.refresh(registro)

    return CalibracionSalida(
        id=registro.id,
        observador=registro.observador,
        espera_obs_min=float(registro.espera_obs_min),
        espera_sistema_min=float(registro.espera_sistema_min)
        if registro.espera_sistema_min is not None
        else None,
        error_min=float(registro.error_min) if registro.error_min is not None else None,
    )


@router.get(
    "/calibraciones/resumen",
    response_model=ResumenCalibracion,
    summary="Error absoluto medio y sesgo del sistema",
)
def resumen_calibracion(
    sesion: Annotated[Session, Depends(obtener_sesion)],
    _usuario: Annotated[Usuario, Depends(solo_lectura)],
    sala: str = Query(description="Codigo de sala"),
) -> ResumenCalibracion:
    obj = obtener_sala(sesion, sala)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Sala desconocida: {sala}")

    registros = list(sesion.scalars(select(Calibracion).where(Calibracion.sala_id == obj.id)))
    errores = [float(r.error_min) for r in registros if r.error_min is not None]
    con_dato = [r for r in registros if r.con_acompanante is not None]

    return ResumenCalibracion(
        sala_id=obj.codigo,
        n_observaciones=len(registros),
        error_absoluto_medio_min=round(sum(abs(e) for e in errores) / len(errores), 2)
        if errores
        else None,
        sesgo_min=round(sum(errores) / len(errores), 2) if errores else None,
        ratio_acompanante_observado=round(
            sum(1 for r in con_dato if r.con_acompanante) / len(con_dato), 3
        )
        if con_dato
        else None,
    )

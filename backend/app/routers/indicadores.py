"""Consultas de estado, curvas e indicadores. Alimentan el tablero web."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import obtener_sesion
from app.models import SalaEspera, Usuario
from app.schemas import (
    EstadoSala,
    FilaComparacion,
    IndicadorDia,
    PuntoCurva,
    RespuestaComparacion,
    RespuestaCurvas,
    RespuestaIndicadores,
    SalaSalida,
)
from app.security import solo_lectura
from app.servicios import curvas
from app.servicios.consultas import (
    contexto_del_dia,
    cruces_de_fecha,
    fechas_con_datos,
    obtener_sala,
    resultado_del_dia,
)
from app.servicios.estimadores import comparar_estimadores
from app.servicios.tiempo import a_local, ahora_utc, fecha_operativa

router = APIRouter(prefix="/v1", tags=["indicadores"])


@router.get("/catalogos/salas", response_model=list[SalaSalida], summary="Salas configuradas")
def listar_salas(
    sesion: Annotated[Session, Depends(obtener_sesion)],
    _usuario: Annotated[Usuario, Depends(solo_lectura)],
) -> list[SalaSalida]:
    salas = sesion.scalars(select(SalaEspera).order_by(SalaEspera.codigo))
    return [
        SalaSalida(
            codigo=s.codigo,
            nombre=s.nombre,
            establecimiento=s.establecimiento.nombre,
            aforo_referencial=s.aforo_referencial,
            ratio_acompanante=float(s.ratio_acompanante or 0),
            estimador=s.estimador,
            activo=s.activo,
        )
        for s in salas
    ]


@router.get(
    "/salas/{codigo}/estado",
    response_model=EstadoSala,
    summary="Ocupacion actual y espera estimada",
)
def estado_sala(
    codigo: str,
    sesion: Annotated[Session, Depends(obtener_sesion)],
    _usuario: Annotated[Usuario, Depends(solo_lectura)],
    fecha: date | None = Query(default=None, description="Fecha operativa; por defecto hoy"),
) -> EstadoSala:
    sala = obtener_sala(sesion, codigo)
    if sala is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Sala desconocida: {codigo}")

    momento = ahora_utc()
    f = fecha or fecha_operativa(momento)

    entradas, salidas = cruces_de_fecha(sesion, sala.id, f)
    ratio = float(sala.ratio_acompanante or 0)
    resultado = resultado_del_dia(sesion, sala, f)

    referencia = a_local(momento)
    if entradas or salidas:
        ultimo = max(entradas[-1:] + salidas[-1:])
        # Si se consulta una fecha pasada, la referencia es el final de esa
        # jornada y no el instante actual.
        if referencia.date() != f:
            referencia = ultimo

    espera_actual = curvas.espera_estimada_actual(
        entradas, salidas, referencia, ratio_acompanante=ratio
    )

    return EstadoSala(
        sala_id=sala.codigo,
        nombre=sala.nombre,
        establecimiento=sala.establecimiento.nombre,
        fecha_operativa=f,
        momento=referencia,
        ocupacion=curvas.ocupacion_en(entradas, salidas, referencia),
        n_entradas=len(entradas),
        n_salidas=len(salidas),
        espera_estimada_min=round(espera_actual, 1) if espera_actual is not None else None,
        espera_p50_min=round(resultado.p50, 1) if resultado.p50 is not None else None,
        espera_p90_min=round(resultado.p90, 1) if resultado.p90 is not None else None,
        ratio_acompanante=ratio,
        muestras=len(resultado.esperas_min),
    )


@router.get(
    "/salas/{codigo}/curvas",
    response_model=RespuestaCurvas,
    summary="Series N_in y N_out de una jornada",
)
def curvas_sala(
    codigo: str,
    sesion: Annotated[Session, Depends(obtener_sesion)],
    _usuario: Annotated[Usuario, Depends(solo_lectura)],
    respuesta: Response = None,
    fecha: date | None = Query(default=None),
    paso_min: int = Query(default=5, ge=1, le=60),
) -> RespuestaCurvas:
    sala = obtener_sala(sesion, codigo)
    if sala is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Sala desconocida: {codigo}")

    f = fecha or fecha_operativa(ahora_utc())
    entradas, salidas = cruces_de_fecha(sesion, sala.id, f)
    puntos = curvas.serie_acumulada(entradas, salidas, paso_min=paso_min)

    if respuesta is not None and f < fecha_operativa(ahora_utc()):
        # Una jornada cerrada ya no cambia: se puede cachear con holgura.
        respuesta.headers["Cache-Control"] = "public, max-age=3600"

    return RespuestaCurvas(
        sala_id=sala.codigo,
        fecha_operativa=f,
        paso_min=paso_min,
        puntos=[PuntoCurva(**p) for p in puntos],
    )


@router.get(
    "/indicadores",
    response_model=RespuestaIndicadores,
    summary="P50 y P90 por establecimiento y rango de fechas",
)
def indicadores(
    sesion: Annotated[Session, Depends(obtener_sesion)],
    _usuario: Annotated[Usuario, Depends(solo_lectura)],
    sala: str = Query(description="Codigo de sala"),
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
) -> RespuestaIndicadores:
    obj = obtener_sala(sesion, sala)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Sala desconocida: {sala}")

    hoy = fecha_operativa(ahora_utc())
    hasta = hasta or hoy
    desde = desde or (hasta - timedelta(days=29))
    if desde > hasta:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El rango de fechas esta invertido")

    filas: list[IndicadorDia] = []
    todas_esperas: list[float] = []

    for f in fechas_con_datos(sesion, obj.id, desde, hasta):
        entradas, salidas = cruces_de_fecha(sesion, obj.id, f)
        resultado = resultado_del_dia(sesion, obj, f)
        todas_esperas.extend(resultado.esperas_min)

        ocupacion_max = 0
        for punto in curvas.serie_acumulada(entradas, salidas, paso_min=5):
            ocupacion_max = max(ocupacion_max, punto["ocupacion"])

        filas.append(
            IndicadorDia(
                sala_id=obj.codigo,
                fecha_operativa=f,
                n_pacientes=len(resultado.esperas_min),
                espera_p50_min=round(resultado.p50, 1) if resultado.p50 is not None else None,
                espera_p90_min=round(resultado.p90, 1) if resultado.p90 is not None else None,
                espera_promedio_min=round(resultado.promedio, 1)
                if resultado.promedio is not None
                else None,
                ocupacion_maxima=ocupacion_max,
                descartados=resultado.descartados,
            )
        )

    p50 = curvas.percentil(todas_esperas, 0.50)
    p90 = curvas.percentil(todas_esperas, 0.90)

    return RespuestaIndicadores(
        desde=desde,
        hasta=hasta,
        filas=filas,
        resumen_p50_min=round(p50, 1) if p50 is not None else None,
        resumen_p90_min=round(p90, 1) if p90 is not None else None,
    )


@router.get(
    "/salas/{codigo}/comparar-estimadores",
    response_model=RespuestaComparacion,
    summary="Compara todos los estimadores sobre la misma jornada",
)
def comparar(
    codigo: str,
    sesion: Annotated[Session, Depends(obtener_sesion)],
    _usuario: Annotated[Usuario, Depends(solo_lectura)],
    fecha: date | None = Query(default=None),
) -> RespuestaComparacion:
    """Ejecuta todos los estimadores registrados sobre los mismos cruces.

    Es el endpoint que sostiene el capitulo de validacion del proyecto: permite
    exhibir cuanto aporta la correccion sin editar codigo ni volver a
    desplegar. Solo es posible porque el calculo esta detras de un Strategy.
    """
    sala = obtener_sala(sesion, codigo)
    if sala is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Sala desconocida: {codigo}")

    f = fecha or fecha_operativa(ahora_utc())
    comparacion = comparar_estimadores(contexto_del_dia(sesion, sala, f))

    return RespuestaComparacion(
        sala_id=sala.codigo,
        fecha_operativa=f,
        estimador_activo=sala.estimador,
        filas=[
            FilaComparacion(
                estimador=nombre,
                descripcion=datos["descripcion"],
                muestras=datos["muestras"],
                descartados=datos["descartados"],
                espera_p50_min=round(datos["p50"], 1) if datos["p50"] is not None else None,
                espera_p90_min=round(datos["p90"], 1) if datos["p90"] is not None else None,
            )
            for nombre, datos in comparacion.items()
        ],
    )

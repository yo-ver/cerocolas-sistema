"""Ramas restantes: latido del agente, cuenta desactivada y corte temporal.

Cada una corresponde a una decision que solo se evalua por su camino falso
durante el uso normal, y que por tanto quedaria sin verificar aunque la
cobertura de sentencias fuera completa.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import SesionLocal
from app.models import Agente, Establecimiento, Usuario
from app.security import hashear_password
from app.servicios import curvas
from app.servicios.consultas import cruces_de_fecha, obtener_sala
from app.servicios.tiempo import a_utc

TZ = timezone(timedelta(hours=-5))


def base_dia(dias_atras: int = 20) -> datetime:
    hoy = datetime.now(TZ).replace(hour=7, minute=0, second=0, microsecond=0)
    return hoy - timedelta(days=dias_atras)


def test_el_lote_actualiza_el_latido_del_agente(cliente, cabecera_agente):
    """Un agente registrado reporta que sigue vivo al enviar.

    Sin este latido, un equipo de borde apagado seria indistinguible de una
    sala vacia: ambos dejan de producir cruces.
    """
    sesion = SesionLocal()
    try:
        establecimiento = sesion.scalar(select(Establecimiento))
        sesion.add(
            Agente(
                establecimiento_id=establecimiento.id,
                hostname="borde-de-prueba",
                version_modelo="yolo11n-coco",
                estado="DESCONECTADO",
            )
        )
        sesion.commit()
    finally:
        sesion.close()

    respuesta = cliente.post(
        "/v1/cruces:lote",
        json={
            "sala_id": "TEST-CE-01",
            "agente": "borde-de-prueba",
            "cruces": [
                {
                    "linea": "ENTRADA_SALA",
                    "direccion": "IN",
                    "ocurrido_en": base_dia(21).isoformat(),
                }
            ],
        },
        headers={**cabecera_agente, "Idempotency-Key": "lote-con-latido"},
    )
    assert respuesta.status_code == 201

    sesion = SesionLocal()
    try:
        agente = sesion.scalar(select(Agente).where(Agente.hostname == "borde-de-prueba"))
        assert agente.estado == "ACTIVO"
        assert agente.ultimo_heartbeat is not None
    finally:
        sesion.close()


def test_una_cuenta_desactivada_pierde_el_acceso(cliente):
    """El token sigue siendo valido, pero la cuenta ya no.

    Comprobar el estado en cada peticion y no solo al emitir el token es lo
    que permite revocar un acceso antes de que expire.
    """
    sesion = SesionLocal()
    try:
        establecimiento = sesion.scalar(select(Establecimiento))
        sesion.add(
            Usuario(
                establecimiento_id=establecimiento.id,
                email="cesado@test.local",
                hash_password=hashear_password("clave-de-prueba"),
                rol="VISOR",
                activo=True,
            )
        )
        sesion.commit()
    finally:
        sesion.close()

    token = cliente.post(
        "/v1/auth/token",
        json={"email": "cesado@test.local", "password": "clave-de-prueba"},
    ).json()["access_token"]
    cabecera = {"Authorization": f"Bearer {token}"}

    assert cliente.get("/v1/catalogos/salas", headers=cabecera).status_code == 200

    sesion = SesionLocal()
    try:
        usuario = sesion.scalar(select(Usuario).where(Usuario.email == "cesado@test.local"))
        usuario.activo = False
        sesion.commit()
    finally:
        sesion.close()

    assert cliente.get("/v1/catalogos/salas", headers=cabecera).status_code == 401


def test_una_cuenta_desactivada_no_obtiene_token(cliente):
    respuesta = cliente.post(
        "/v1/auth/token",
        json={"email": "cesado@test.local", "password": "clave-de-prueba"},
    )
    assert respuesta.status_code == 401


def test_el_corte_temporal_recorta_la_jornada(cliente, cabecera_agente):
    """Consultar el estado a media jornada no debe ver el futuro.

    Es la rama que permite reconstruir como se veia la sala a una hora dada,
    necesaria para comparar el sistema con una observacion manual puntual.
    """
    inicio = base_dia(25)
    cruces = []
    for i in range(6):
        cruces.append(
            {
                "linea": "ENTRADA_SALA",
                "direccion": "IN",
                "ocurrido_en": (inicio + timedelta(minutes=i * 10)).isoformat(),
            }
        )
    cliente.post(
        "/v1/cruces:lote",
        json={"sala_id": "TEST-CE-01", "cruces": cruces},
        headers={**cabecera_agente, "Idempotency-Key": "lote-corte-temporal"},
    )

    sesion = SesionLocal()
    try:
        sala = obtener_sala(sesion, "TEST-CE-01")
        fecha = inicio.date()

        todas, _ = cruces_de_fecha(sesion, sala.id, fecha)
        hasta_media = a_utc(inicio + timedelta(minutes=25))
        parciales, _ = cruces_de_fecha(sesion, sala.id, fecha, hasta=hasta_media)
    finally:
        sesion.close()

    assert len(todas) == 6
    assert len(parciales) == 3


def test_se_detiene_cuando_hay_mas_salidas_que_entradas():
    """Mas salidas que entradas indica una linea de entrada que perdio cruces.

    El emparejamiento se detiene en lugar de inventar una entrada, porque un
    tiempo de espera fabricado es peor que un dato faltante.
    """
    base = datetime(2026, 8, 15, 7, 0, tzinfo=TZ)
    entradas = [base, base + timedelta(minutes=5)]
    salidas = [base + timedelta(minutes=m) for m in (30, 35, 40, 45)]

    resultado = curvas.calcular(entradas, salidas)

    assert len(resultado.esperas_min) == 2
    assert resultado.n_salidas == 4
    assert resultado.ocupacion_final == 0


# --- consulta de la jornada en curso ---------------------------------------


def test_estado_de_la_jornada_en_curso_usa_el_instante_actual(
    cliente, cabecera_agente, cabecera_visor
):
    """Consultar hoy es distinto de consultar una jornada cerrada.

    En una jornada cerrada la referencia es el ultimo cruce registrado; en la
    de hoy es el reloj, porque la sala sigue llenandose. Confundirlos daria una
    ocupacion congelada en el ultimo evento.
    """
    ahora = datetime.now(TZ)
    inicio = ahora - timedelta(hours=2)

    cruces = []
    for i in range(8):
        cruces.append(
            {
                "linea": "ENTRADA_SALA",
                "direccion": "IN",
                "ocurrido_en": (inicio + timedelta(minutes=i * 6)).isoformat(),
            }
        )
    for i in range(5):
        cruces.append(
            {
                "linea": "PUERTA_CONS_01",
                "direccion": "OUT",
                "ocurrido_en": (inicio + timedelta(minutes=40 + i * 6)).isoformat(),
            }
        )

    respuesta = cliente.post(
        "/v1/cruces:lote",
        json={"sala_id": "TEST-CE-01", "cruces": cruces},
        headers={**cabecera_agente, "Idempotency-Key": "lote-jornada-en-curso"},
    )
    assert respuesta.json()["aceptados"] == 13

    estado = cliente.get("/v1/salas/TEST-CE-01/estado", headers=cabecera_visor)
    assert estado.status_code == 200
    datos = estado.json()
    assert datos["n_entradas"] >= 8
    assert datos["ocupacion"] >= 3


def test_la_jornada_en_curso_no_se_cachea(cliente, cabecera_visor):
    """Una jornada abierta cambia cada minuto; una cerrada ya no.

    Cachear la de hoy mostraria una sala congelada en el tablero.
    """
    hoy = cliente.get("/v1/salas/TEST-CE-01/curvas", headers=cabecera_visor)
    assert hoy.status_code == 200
    assert "Cache-Control" not in hoy.headers

    pasada = cliente.get("/v1/salas/TEST-CE-01/curvas?fecha=2020-01-01", headers=cabecera_visor)
    assert pasada.headers.get("Cache-Control") == "public, max-age=3600"

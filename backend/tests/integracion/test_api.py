"""Pruebas de la API: autenticacion, ingesta idempotente e indicadores."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=-5))


def base_dia(dias_atras: int = 1) -> datetime:
    """Una jornada pasada, para no depender de la hora en que corran las pruebas."""
    hoy = datetime.now(TZ).replace(hour=6, minute=0, second=0, microsecond=0)
    return hoy - timedelta(days=dias_atras)


def construir_cruces(inicio: datetime, n: int = 12) -> list[dict]:
    """n pacientes entran cada 5 min y salen 40 min despues de entrar."""
    cruces = []
    for i in range(n):
        cruces.append(
            {
                "linea": "ENTRADA_SALA",
                "direccion": "IN",
                "ocurrido_en": (inicio + timedelta(minutes=i * 5)).isoformat(),
                "confianza": 0.9,
            }
        )
    for i in range(n):
        cruces.append(
            {
                "linea": f"PUERTA_CONS_{(i % 2) + 1:02d}",
                "direccion": "OUT",
                "ocurrido_en": (inicio + timedelta(minutes=i * 5 + 40)).isoformat(),
                "confianza": 0.88,
            }
        )
    return cruces


# --- autenticacion y permisos ---------------------------------------------


def test_salud_no_requiere_token(cliente):
    respuesta = cliente.get("/salud")
    assert respuesta.status_code == 200
    assert respuesta.json()["estado"] == "ok"


def test_sin_token_rechaza(cliente):
    respuesta = cliente.get("/v1/catalogos/salas")
    assert respuesta.status_code == 401


def test_credenciales_invalidas(cliente):
    respuesta = cliente.post(
        "/v1/auth/token", json={"email": "visor@test.local", "password": "incorrecta"}
    )
    assert respuesta.status_code == 401


def test_agente_no_puede_leer_indicadores(cliente, cabecera_agente):
    """El agente solo escribe. Si el equipo de borde se compromete, no expone datos."""
    respuesta = cliente.get("/v1/catalogos/salas", headers=cabecera_agente)
    assert respuesta.status_code == 403


def test_visor_no_puede_ingestar(cliente, cabecera_visor):
    respuesta = cliente.post(
        "/v1/cruces:lote",
        json={"sala_id": "TEST-CE-01", "cruces": construir_cruces(base_dia(), 1)},
        headers={**cabecera_visor, "Idempotency-Key": "clave-visor"},
    )
    assert respuesta.status_code == 403


# --- ingesta ---------------------------------------------------------------


def test_ingesta_exige_clave_de_idempotencia(cliente, cabecera_agente):
    respuesta = cliente.post(
        "/v1/cruces:lote",
        json={"sala_id": "TEST-CE-01", "cruces": construir_cruces(base_dia(), 1)},
        headers=cabecera_agente,
    )
    assert respuesta.status_code == 400
    assert "Idempotency-Key" in respuesta.json()["detail"]


def test_ingesta_y_deduplicacion(cliente, cabecera_agente):
    """El reintento tras una caida de red no debe duplicar cruces."""
    inicio = base_dia(3)
    cruces = construir_cruces(inicio, 12)
    cabeceras = {**cabecera_agente, "Idempotency-Key": "lote-prueba-001"}
    cuerpo = {"sala_id": "TEST-CE-01", "agente": None, "cruces": cruces}

    primera = cliente.post("/v1/cruces:lote", json=cuerpo, headers=cabeceras)
    assert primera.status_code == 201
    datos = primera.json()
    assert datos["aceptados"] == 24
    assert datos["duplicado"] is False

    segunda = cliente.post("/v1/cruces:lote", json=cuerpo, headers=cabeceras)
    assert segunda.status_code == 201
    repetida = segunda.json()
    assert repetida["duplicado"] is True
    assert repetida["lote_id"] == datos["lote_id"]
    assert repetida["aceptados"] == 24  # no se insertaron cruces nuevos


def test_rechaza_linea_desconocida(cliente, cabecera_agente):
    respuesta = cliente.post(
        "/v1/cruces:lote",
        json={
            "sala_id": "TEST-CE-01",
            "cruces": [
                {
                    "linea": "LINEA_INEXISTENTE",
                    "direccion": "IN",
                    "ocurrido_en": base_dia().isoformat(),
                }
            ],
        },
        headers={**cabecera_agente, "Idempotency-Key": "lote-linea-mala"},
    )
    assert respuesta.status_code == 201
    datos = respuesta.json()
    assert datos["aceptados"] == 0
    assert datos["rechazados"] == 1
    assert "desconocida" in datos["detalles"][0]["motivo"].lower()


def test_rechaza_direccion_incoherente(cliente, cabecera_agente):
    """Una linea de ENTRADA que reporta OUT indica geometria invertida."""
    respuesta = cliente.post(
        "/v1/cruces:lote",
        json={
            "sala_id": "TEST-CE-01",
            "cruces": [
                {
                    "linea": "ENTRADA_SALA",
                    "direccion": "OUT",
                    "ocurrido_en": base_dia().isoformat(),
                }
            ],
        },
        headers={**cabecera_agente, "Idempotency-Key": "lote-direccion-mala"},
    )
    assert respuesta.json()["rechazados"] == 1


def test_rechaza_fecha_sin_zona_horaria(cliente, cabecera_agente):
    """La base manual no tenia zona horaria y eso genero ambiguedades."""
    respuesta = cliente.post(
        "/v1/cruces:lote",
        json={
            "sala_id": "TEST-CE-01",
            "cruces": [
                {
                    "linea": "ENTRADA_SALA",
                    "direccion": "IN",
                    "ocurrido_en": "2026-08-10T06:00:00",
                }
            ],
        },
        headers={**cabecera_agente, "Idempotency-Key": "lote-sin-tz"},
    )
    assert respuesta.status_code == 422


def test_rechaza_sala_desconocida(cliente, cabecera_agente):
    respuesta = cliente.post(
        "/v1/cruces:lote",
        json={"sala_id": "NO-EXISTE", "cruces": construir_cruces(base_dia(), 1)},
        headers={**cabecera_agente, "Idempotency-Key": "lote-sala-mala"},
    )
    assert respuesta.status_code == 404


# --- consultas -------------------------------------------------------------


def test_estado_y_curvas(cliente, cabecera_agente, cabecera_visor):
    inicio = base_dia(5)
    cliente.post(
        "/v1/cruces:lote",
        json={"sala_id": "TEST-CE-01", "cruces": construir_cruces(inicio, 10)},
        headers={**cabecera_agente, "Idempotency-Key": "lote-estado-001"},
    )
    fecha = inicio.date().isoformat()

    estado = cliente.get(f"/v1/salas/TEST-CE-01/estado?fecha={fecha}", headers=cabecera_visor)
    assert estado.status_code == 200
    datos = estado.json()
    assert datos["n_entradas"] == 10
    assert datos["n_salidas"] == 10
    assert datos["espera_p50_min"] == 40.0

    curvas_resp = cliente.get(
        f"/v1/salas/TEST-CE-01/curvas?fecha={fecha}&paso_min=5", headers=cabecera_visor
    )
    assert curvas_resp.status_code == 200
    puntos = curvas_resp.json()["puntos"]
    assert puntos
    assert puntos[-1]["n_in"] == 10
    assert puntos[-1]["n_out"] == 10


def test_indicadores_rango(cliente, cabecera_agente, cabecera_visor):
    inicio = base_dia(7)
    cliente.post(
        "/v1/cruces:lote",
        json={"sala_id": "TEST-CE-01", "cruces": construir_cruces(inicio, 8)},
        headers={**cabecera_agente, "Idempotency-Key": "lote-indicadores-001"},
    )
    fecha = inicio.date().isoformat()

    respuesta = cliente.get(
        f"/v1/indicadores?sala=TEST-CE-01&desde={fecha}&hasta={fecha}", headers=cabecera_visor
    )
    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert len(datos["filas"]) == 1
    fila = datos["filas"][0]
    assert fila["n_pacientes"] == 8
    assert fila["espera_p50_min"] == 40.0
    assert datos["resumen_p50_min"] == 40.0


def test_indicadores_rango_invertido(cliente, cabecera_visor):
    respuesta = cliente.get(
        "/v1/indicadores?sala=TEST-CE-01&desde=2026-08-20&hasta=2026-08-10",
        headers=cabecera_visor,
    )
    assert respuesta.status_code == 400


# --- calibracion -----------------------------------------------------------


def test_calibracion_calcula_error(cliente, cabecera_agente, cabecera_gestor, cabecera_visor):
    inicio = base_dia(9)
    cliente.post(
        "/v1/cruces:lote",
        json={"sala_id": "TEST-CE-01", "cruces": construir_cruces(inicio, 10)},
        headers={**cabecera_agente, "Idempotency-Key": "lote-calibracion-001"},
    )

    # Observacion manual: el paciente espero 45 min; el sistema estima 40.
    respuesta = cliente.post(
        "/v1/calibraciones",
        json={
            "sala_id": "TEST-CE-01",
            "observador": "observador-1",
            "t_entrada_obs": (inicio - timedelta(minutes=5)).isoformat(),
            "t_salida_obs": (inicio + timedelta(minutes=40)).isoformat(),
            "con_acompanante": True,
        },
        headers=cabecera_gestor,
    )
    assert respuesta.status_code == 201
    datos = respuesta.json()
    assert datos["espera_obs_min"] == 45.0
    assert datos["espera_sistema_min"] == 40.0
    assert datos["error_min"] == -5.0

    resumen = cliente.get("/v1/calibraciones/resumen?sala=TEST-CE-01", headers=cabecera_visor)
    assert resumen.status_code == 200
    assert resumen.json()["error_absoluto_medio_min"] == 5.0


def test_calibracion_exige_orden_temporal(cliente, cabecera_gestor):
    ahora = base_dia(2)
    respuesta = cliente.post(
        "/v1/calibraciones",
        json={
            "sala_id": "TEST-CE-01",
            "observador": "observador-1",
            "t_entrada_obs": ahora.isoformat(),
            "t_salida_obs": (ahora - timedelta(minutes=10)).isoformat(),
        },
        headers=cabecera_gestor,
    )
    assert respuesta.status_code == 400

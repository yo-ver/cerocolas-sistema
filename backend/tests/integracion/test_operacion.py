"""Pruebas de integracion complementarias.

Cubren tres grupos de caminos que la suite original dejaba sin ejercitar: el
registro de aforo, el catalogo de salas y las ramas de error que solo aparecen
cuando la operacion va mal —agente desconocido, marca de tiempo futura, sala
inexistente, jornada sin datos—. Son exactamente los caminos que no se
recorren durante una demostracion exitosa y donde, por eso mismo, los defectos
sobreviven.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=-5))


def base_dia(dias_atras: int = 1) -> datetime:
    hoy = datetime.now(TZ).replace(hour=7, minute=0, second=0, microsecond=0)
    return hoy - timedelta(days=dias_atras)


# --- catalogo --------------------------------------------------------------


def test_catalogo_lista_las_salas_configuradas(cliente, cabecera_visor):
    respuesta = cliente.get("/v1/catalogos/salas", headers=cabecera_visor)

    assert respuesta.status_code == 200
    salas = respuesta.json()
    assert len(salas) >= 1

    sala = next(item for item in salas if item["codigo"] == "TEST-CE-01")
    assert sala["establecimiento"] == "Hospital de prueba"
    assert sala["activo"] is True
    assert isinstance(sala["ratio_acompanante"], float)


# --- aforo -----------------------------------------------------------------


def test_registra_conteo_directo_de_ocupacion(cliente, cabecera_agente):
    """El aforo es el ancla que corrige la deriva del calculo acumulado."""
    respuesta = cliente.post(
        "/v1/aforos",
        json={
            "sala_id": "TEST-CE-01",
            "ocurrido_en": base_dia(2).isoformat(),
            "conteo": 42,
            "metodo": "VISION",
            "confianza": 0.9,
        },
        headers=cabecera_agente,
    )

    assert respuesta.status_code == 201
    assert respuesta.json()["registrado"] is True


def test_aforo_rechaza_sala_desconocida(cliente, cabecera_agente):
    respuesta = cliente.post(
        "/v1/aforos",
        json={
            "sala_id": "NO-EXISTE",
            "ocurrido_en": base_dia().isoformat(),
            "conteo": 10,
        },
        headers=cabecera_agente,
    )
    assert respuesta.status_code == 404


def test_aforo_rechaza_conteo_negativo(cliente, cabecera_agente):
    """Una ocupacion negativa no es un dato: es un defecto del agente."""
    respuesta = cliente.post(
        "/v1/aforos",
        json={
            "sala_id": "TEST-CE-01",
            "ocurrido_en": base_dia().isoformat(),
            "conteo": -5,
        },
        headers=cabecera_agente,
    )
    assert respuesta.status_code == 422


def test_visor_no_puede_registrar_aforo(cliente, cabecera_visor):
    respuesta = cliente.post(
        "/v1/aforos",
        json={
            "sala_id": "TEST-CE-01",
            "ocurrido_en": base_dia().isoformat(),
            "conteo": 10,
        },
        headers=cabecera_visor,
    )
    assert respuesta.status_code == 403


# --- ingesta: caminos de error --------------------------------------------


def test_rechaza_cruce_con_marca_de_tiempo_futura(cliente, cabecera_agente):
    """Un instante futuro delata el reloj del equipo de borde desajustado.

    La base manual de KoboCollect contenia registros de 10 110 minutos por
    errores de fecha de este tipo.
    """
    futuro = datetime.now(TZ) + timedelta(hours=3)
    respuesta = cliente.post(
        "/v1/cruces:lote",
        json={
            "sala_id": "TEST-CE-01",
            "cruces": [
                {
                    "linea": "ENTRADA_SALA",
                    "direccion": "IN",
                    "ocurrido_en": futuro.isoformat(),
                }
            ],
        },
        headers={**cabecera_agente, "Idempotency-Key": "lote-futuro"},
    )

    assert respuesta.status_code == 201
    datos = respuesta.json()
    assert datos["aceptados"] == 0
    assert "futuro" in datos["detalles"][0]["motivo"].lower()


def test_acepta_lote_con_agente_declarado(cliente, cabecera_agente):
    """Un hostname no registrado no debe impedir la ingesta.

    Perder mediciones porque el catalogo de agentes esta desactualizado seria
    un intercambio pesimo: el hostname es metadato, los cruces son el dato.
    """
    respuesta = cliente.post(
        "/v1/cruces:lote",
        json={
            "sala_id": "TEST-CE-01",
            "agente": "agente-no-registrado",
            "cruces": [
                {
                    "linea": "ENTRADA_SALA",
                    "direccion": "IN",
                    "ocurrido_en": base_dia(4).isoformat(),
                    "confianza": 0.9,
                }
            ],
        },
        headers={**cabecera_agente, "Idempotency-Key": "lote-agente-desconocido"},
    )

    assert respuesta.status_code == 201
    assert respuesta.json()["aceptados"] == 1


def test_lote_vacio_se_rechaza_en_validacion(cliente, cabecera_agente):
    respuesta = cliente.post(
        "/v1/cruces:lote",
        json={"sala_id": "TEST-CE-01", "cruces": []},
        headers={**cabecera_agente, "Idempotency-Key": "lote-vacio"},
    )
    assert respuesta.status_code == 422


def test_confianza_fuera_de_rango_se_rechaza(cliente, cabecera_agente):
    respuesta = cliente.post(
        "/v1/cruces:lote",
        json={
            "sala_id": "TEST-CE-01",
            "cruces": [
                {
                    "linea": "ENTRADA_SALA",
                    "direccion": "IN",
                    "ocurrido_en": base_dia().isoformat(),
                    "confianza": 1.5,
                }
            ],
        },
        headers={**cabecera_agente, "Idempotency-Key": "lote-confianza-mala"},
    )
    assert respuesta.status_code == 422


# --- consultas sobre jornadas sin datos ------------------------------------


def test_estado_de_jornada_sin_movimiento(cliente, cabecera_visor):
    """Una sala que aun no abrio devuelve ceros, no un error."""
    respuesta = cliente.get("/v1/salas/TEST-CE-01/estado?fecha=2020-01-01", headers=cabecera_visor)

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["n_entradas"] == 0
    assert datos["ocupacion"] == 0
    assert datos["espera_p50_min"] is None
    assert datos["espera_estimada_min"] == 0.0


def test_curvas_de_jornada_sin_movimiento(cliente, cabecera_visor):
    respuesta = cliente.get("/v1/salas/TEST-CE-01/curvas?fecha=2020-01-01", headers=cabecera_visor)
    assert respuesta.status_code == 200
    assert respuesta.json()["puntos"] == []


def test_indicadores_de_rango_sin_datos(cliente, cabecera_visor):
    respuesta = cliente.get(
        "/v1/indicadores?sala=TEST-CE-01&desde=2020-01-01&hasta=2020-01-31",
        headers=cabecera_visor,
    )

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["filas"] == []
    assert datos["resumen_p50_min"] is None


def test_indicadores_usa_rango_por_defecto(cliente, cabecera_visor):
    """Sin fechas, se toman los ultimos treinta dias."""
    respuesta = cliente.get("/v1/indicadores?sala=TEST-CE-01", headers=cabecera_visor)

    assert respuesta.status_code == 200
    datos = respuesta.json()
    desde = datetime.fromisoformat(datos["desde"])
    hasta = datetime.fromisoformat(datos["hasta"])
    assert (hasta - desde).days == 29


def test_estado_de_sala_desconocida(cliente, cabecera_visor):
    respuesta = cliente.get("/v1/salas/NO-EXISTE/estado", headers=cabecera_visor)
    assert respuesta.status_code == 404


def test_curvas_de_sala_desconocida(cliente, cabecera_visor):
    respuesta = cliente.get("/v1/salas/NO-EXISTE/curvas", headers=cabecera_visor)
    assert respuesta.status_code == 404


def test_indicadores_de_sala_desconocida(cliente, cabecera_visor):
    respuesta = cliente.get("/v1/indicadores?sala=NO-EXISTE", headers=cabecera_visor)
    assert respuesta.status_code == 404


# --- calibracion: caminos de error -----------------------------------------


def test_calibracion_de_sala_desconocida(cliente, cabecera_gestor):
    respuesta = cliente.post(
        "/v1/calibraciones",
        json={
            "sala_id": "NO-EXISTE",
            "observador": "obs",
            "t_entrada_obs": base_dia().isoformat(),
            "t_salida_obs": (base_dia() + timedelta(minutes=30)).isoformat(),
        },
        headers=cabecera_gestor,
    )
    assert respuesta.status_code == 404


def test_resumen_de_calibracion_de_sala_desconocida(cliente, cabecera_visor):
    respuesta = cliente.get("/v1/calibraciones/resumen?sala=NO-EXISTE", headers=cabecera_visor)
    assert respuesta.status_code == 404


def test_calibracion_sin_estimacion_del_sistema(cliente, cabecera_gestor):
    """Si no hay cruces esa jornada, se guarda la observacion sin error.

    Descartar la observacion manual seria perder trabajo de campo irrepetible;
    el error se calculara cuando los cruces se hayan ingerido.
    """
    momento = base_dia(400)
    respuesta = cliente.post(
        "/v1/calibraciones",
        json={
            "sala_id": "TEST-CE-01",
            "observador": "observador-sin-datos",
            "t_entrada_obs": momento.isoformat(),
            "t_salida_obs": (momento + timedelta(minutes=25)).isoformat(),
        },
        headers=cabecera_gestor,
    )

    assert respuesta.status_code == 201
    datos = respuesta.json()
    assert datos["espera_obs_min"] == 25.0
    assert datos["espera_sistema_min"] is None
    assert datos["error_min"] is None


# --- comparación de estimadores (Strategy expuesto por la API) -------------


def test_compara_los_estimadores_sobre_la_misma_jornada(cliente, cabecera_agente, cabecera_visor):
    """El endpoint que sostiene el capítulo de validación del proyecto.

    Ejecuta todos los estimadores registrados sobre los mismos cruces. Solo es
    posible porque el cálculo está detrás de un Strategy: con el algoritmo
    cableado, comparar exigiría editar código y volver a desplegar.
    """
    inicio = base_dia(30)
    cruces = []
    for i in range(12):
        cruces.append(
            {
                "linea": "ENTRADA_SALA",
                "direccion": "IN",
                "ocurrido_en": (inicio + timedelta(minutes=i * 5)).isoformat(),
            }
        )
        # Un acompañante de cada dos pacientes: entra pero nunca sale.
        if i % 2 == 0:
            cruces.append(
                {
                    "linea": "ENTRADA_SALA",
                    "direccion": "IN",
                    "ocurrido_en": (inicio + timedelta(minutes=i * 5, seconds=20)).isoformat(),
                }
            )
    for i in range(12):
        cruces.append(
            {
                "linea": "PUERTA_CONS_01",
                "direccion": "OUT",
                "ocurrido_en": (inicio + timedelta(minutes=50 + i * 5)).isoformat(),
            }
        )

    cliente.post(
        "/v1/cruces:lote",
        json={"sala_id": "TEST-CE-01", "cruces": cruces},
        headers={**cabecera_agente, "Idempotency-Key": "lote-comparacion"},
    )

    # La sala se configura con el factor que corresponde a los datos: un
    # acompanante cada dos pacientes. Con factor cero los dos estimadores
    # coinciden por definicion, y la comparacion no diria nada.
    from sqlalchemy import select

    from app.db import SesionLocal
    from app.models import SalaEspera

    sesion = SesionLocal()
    try:
        sala = sesion.scalar(select(SalaEspera).where(SalaEspera.codigo == "TEST-CE-01"))
        anterior = float(sala.ratio_acompanante or 0)
        sala.ratio_acompanante = 0.5
        sesion.commit()
    finally:
        sesion.close()

    fecha = inicio.date().isoformat()
    respuesta = cliente.get(
        f"/v1/salas/TEST-CE-01/comparar-estimadores?fecha={fecha}",
        headers=cabecera_visor,
    )

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["estimador_activo"] == "fifo_corregido"

    por_nombre = {fila["estimador"]: fila for fila in datos["filas"]}
    assert set(por_nombre) == {"fifo_corregido", "fifo_estricto", "ventana_movil"}
    for fila in por_nombre.values():
        assert fila["muestras"] > 0
        assert fila["descripcion"]

    # El estimador sin correccion debe sobrestimar: es la razon de conservarlo.
    assert (
        por_nombre["fifo_estricto"]["espera_p50_min"]
        > por_nombre["fifo_corregido"]["espera_p50_min"]
    )

    sesion = SesionLocal()
    try:
        sala = sesion.scalar(select(SalaEspera).where(SalaEspera.codigo == "TEST-CE-01"))
        sala.ratio_acompanante = anterior
        sesion.commit()
    finally:
        sesion.close()


def test_comparar_estimadores_de_sala_desconocida(cliente, cabecera_visor):
    respuesta = cliente.get("/v1/salas/NO-EXISTE/comparar-estimadores", headers=cabecera_visor)
    assert respuesta.status_code == 404


def test_el_catalogo_expone_el_estimador_de_cada_sala(cliente, cabecera_visor):
    salas = cliente.get("/v1/catalogos/salas", headers=cabecera_visor).json()
    sala = next(s for s in salas if s["codigo"] == "TEST-CE-01")
    assert sala["estimador"] == "fifo_corregido"

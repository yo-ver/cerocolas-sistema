"""Pruebas del emisor contra un servidor simulado.

Se usa un transporte de prueba de httpx en lugar de un servidor real porque lo
que interesa verificar no es que la API responda —eso ya se prueba en el
backend— sino como reacciona el agente cuando la red falla: si reintenta, si
renueva la credencial, si conserva la clave de idempotencia entre intentos y si
deja de insistir cuando el problema no se arregla insistiendo.

Reproducir esos escenarios con un servidor real exigiria provocar caidas
deliberadas; con un transporte simulado son tres lineas y son deterministas.
"""

from __future__ import annotations

import httpx
import pytest

from agente.emisor import Emisor, ErrorAutenticacion

FILAS = [
    {
        "id": 1,
        "linea": "ENTRADA_SALA",
        "direccion": "IN",
        "ocurrido_en": "2026-08-15T07:12:04-05:00",
        "confianza": 0.91,
        "track_id": 12,
    },
    {
        "id": 2,
        "linea": "PUERTA_CONS_01",
        "direccion": "OUT",
        "ocurrido_en": "2026-08-15T07:52:31-05:00",
        "confianza": 0.87,
        "track_id": 12,
    },
]


def construir_emisor(manejador, **extra) -> Emisor:
    emisor = Emisor(
        url_api="http://servidor-de-prueba",
        sala_id="LOR-CE-01",
        hostname="lorena-edge-01",
        email="agente@hospital.pe",
        password="clave",
        **extra,
    )
    emisor.cliente = httpx.Client(transport=httpx.MockTransport(manejador))
    return emisor


def respuesta_token(peticion):
    return httpx.Response(200, json={"access_token": "token-de-prueba"})


# --- clave de idempotencia -------------------------------------------------


def test_la_clave_depende_del_contenido_del_lote():
    """Debe ser determinista para que un reintento no duplique cruces."""
    primera = Emisor.clave_idempotencia("LOR-CE-01", FILAS)
    segunda = Emisor.clave_idempotencia("LOR-CE-01", FILAS)

    assert primera == segunda
    assert len(primera) == 40


def test_lotes_distintos_producen_claves_distintas():
    otras = [{**FILAS[0], "id": 99}, FILAS[1]]
    assert Emisor.clave_idempotencia("LOR-CE-01", FILAS) != Emisor.clave_idempotencia(
        "LOR-CE-01", otras
    )


def test_la_misma_secuencia_en_otra_sala_produce_otra_clave():
    """Dos agentes pueden numerar sus colas locales igual desde cero."""
    assert Emisor.clave_idempotencia("LOR-CE-01", FILAS) != Emisor.clave_idempotencia(
        "REG-CE-01", FILAS
    )


# --- envio correcto --------------------------------------------------------


def test_envia_el_lote_con_credencial_y_clave():
    vistas = {}

    def manejador(peticion: httpx.Request) -> httpx.Response:
        if peticion.url.path.endswith("/token"):
            return respuesta_token(peticion)
        vistas["auth"] = peticion.headers.get("Authorization")
        vistas["clave"] = peticion.headers.get("Idempotency-Key")
        vistas["cuerpo"] = peticion.read().decode()
        return httpx.Response(201, json={"aceptados": 2, "rechazados": 0})

    resultado = construir_emisor(manejador).enviar_lote(FILAS)

    assert resultado["aceptados"] == 2
    assert vistas["auth"] == "Bearer token-de-prueba"
    assert vistas["clave"] == Emisor.clave_idempotencia("LOR-CE-01", FILAS)
    assert "PUERTA_CONS_01" in vistas["cuerpo"]


def test_un_lote_vacio_no_genera_peticion():
    def manejador(peticion):  # pragma: no cover - no debe invocarse
        raise AssertionError("no deberia enviarse nada")

    assert construir_emisor(manejador).enviar_lote([]) is None


def test_reconoce_un_lote_ya_procesado():
    """El servidor confirma la deduplicacion; el agente puede vaciar la cola."""

    def manejador(peticion):
        if peticion.url.path.endswith("/token"):
            return respuesta_token(peticion)
        return httpx.Response(201, json={"aceptados": 2, "rechazados": 0, "duplicado": True})

    resultado = construir_emisor(manejador).enviar_lote(FILAS)
    assert resultado["duplicado"] is True


def test_informa_cuando_el_servidor_rechaza_cruces():
    """Un rechazo suele significar geometria de linea invertida."""

    def manejador(peticion):
        if peticion.url.path.endswith("/token"):
            return respuesta_token(peticion)
        return httpx.Response(
            201,
            json={
                "aceptados": 1,
                "rechazados": 1,
                "detalles": [{"indice": 0, "motivo": "La linea X es de tipo ENTRADA"}],
            },
        )

    resultado = construir_emisor(manejador).enviar_lote(FILAS)
    assert resultado["rechazados"] == 1


# --- fallos de red y de credencial -----------------------------------------


def test_renueva_la_credencial_cuando_expira():
    """Una jornada dura ocho horas: el agente debe sobrevivirla sin ayuda."""
    estado = {"tokens": 0, "primer_intento": True}

    def manejador(peticion):
        if peticion.url.path.endswith("/token"):
            estado["tokens"] += 1
            return httpx.Response(200, json={"access_token": f"t{estado['tokens']}"})
        if estado["primer_intento"]:
            estado["primer_intento"] = False
            return httpx.Response(401)
        return httpx.Response(201, json={"aceptados": 2, "rechazados": 0})

    resultado = construir_emisor(manejador).enviar_lote(FILAS)

    assert resultado["aceptados"] == 2
    assert estado["tokens"] == 2  # la credencial se pidio de nuevo


def test_reintenta_ante_un_error_del_servidor():
    intentos = {"n": 0}

    def manejador(peticion):
        if peticion.url.path.endswith("/token"):
            return respuesta_token(peticion)
        intentos["n"] += 1
        if intentos["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(201, json={"aceptados": 2, "rechazados": 0})

    emisor = construir_emisor(manejador, reintentos=5)
    emisor.enviar_lote.__globals__["time"].sleep = lambda _s: None

    resultado = emisor.enviar_lote(FILAS)

    assert resultado["aceptados"] == 2
    assert intentos["n"] == 3


def test_desiste_tras_agotar_los_reintentos():
    """Al desistir devuelve None y la cola conserva las filas sin confirmar."""

    def manejador(peticion):
        if peticion.url.path.endswith("/token"):
            return respuesta_token(peticion)
        return httpx.Response(500)

    emisor = construir_emisor(manejador, reintentos=2)
    emisor.enviar_lote.__globals__["time"].sleep = lambda _s: None

    assert emisor.enviar_lote(FILAS) is None


def test_no_reintenta_ante_un_error_de_contenido():
    """Un 400 no se arregla insistiendo: insistir solo castiga a la red."""
    intentos = {"n": 0}

    def manejador(peticion):
        if peticion.url.path.endswith("/token"):
            return respuesta_token(peticion)
        intentos["n"] += 1
        return httpx.Response(404, text="Sala desconocida")

    assert construir_emisor(manejador).enviar_lote(FILAS) is None
    assert intentos["n"] == 1


def test_sobrevive_a_una_caida_de_red():
    def manejador(peticion):
        if peticion.url.path.endswith("/token"):
            return respuesta_token(peticion)
        raise httpx.ConnectError("red caida")

    emisor = construir_emisor(manejador, reintentos=2)
    emisor.enviar_lote.__globals__["time"].sleep = lambda _s: None

    assert emisor.enviar_lote(FILAS) is None


def test_una_credencial_invalida_se_reporta_con_claridad():
    def manejador(peticion):
        return httpx.Response(401, text="Credenciales invalidas")

    with pytest.raises(ErrorAutenticacion, match="autenticar"):
        construir_emisor(manejador).autenticar()


# --- aforo -----------------------------------------------------------------


def test_envia_el_conteo_de_ocupacion():
    def manejador(peticion):
        if peticion.url.path.endswith("/token"):
            return respuesta_token(peticion)
        assert "aforos" in peticion.url.path
        return httpx.Response(201, json={"registrado": True})

    emisor = construir_emisor(manejador)
    emisor.autenticar()

    assert emisor.enviar_aforo(37) is True


def test_el_aforo_no_interrumpe_la_medicion_si_falla():
    """El aforo es un apoyo: su fallo no puede detener el conteo de cruces."""

    def manejador(peticion):
        if peticion.url.path.endswith("/token"):
            return respuesta_token(peticion)
        raise httpx.ConnectError("red caida")

    emisor = construir_emisor(manejador)
    emisor.autenticar()

    assert emisor.enviar_aforo(37) is False


def test_el_aforo_pide_credencial_nueva_ante_un_401():
    def manejador(peticion):
        if peticion.url.path.endswith("/token"):
            return respuesta_token(peticion)
        return httpx.Response(401)

    emisor = construir_emisor(manejador)
    emisor.autenticar()

    assert emisor.enviar_aforo(37) is False


def test_cerrar_libera_el_cliente():
    emisor = construir_emisor(respuesta_token)
    emisor.cerrar()
    assert emisor.cliente.is_closed

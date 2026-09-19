"""Prueba de humo del agente completo.

Ejecuta el bucle real —captura, deteccion, conteo, cola y envio— durante unos
segundos contra un servidor simulado. Es el equivalente para el agente de lo
que la prueba del punto de entrada es para la imagen de contenedor: no verifica
una regla concreta, verifica que las piezas encajan y que el proceso arranca y
se detiene sin dejar mediciones atrapadas.

Aporta lo que ninguna prueba de componente puede dar: que el hilo de envio y el
de captura convivan sin bloquearse, que la cola se vacie al cerrar y que un
fallo del servidor no detenga la medicion.
"""

from __future__ import annotations

import threading

import httpx
import pytest
import yaml

from agente.captura import CapturaRTSP, CapturaSimulada
from agente.config import cargar
from agente.principal import Agente

CONFIG = {
    "sala_id": "LOR-CE-01",
    "hostname": "lorena-edge-01",
    "nivel_registro": "WARNING",
    "api": {"url": "http://servidor-de-prueba", "email": "a@b.pe", "password": "x"},
    "video": {"fuente": "simulada", "fps_objetivo": 50},
    "detector": {
        "tipo": "simulado",
        "simulado": {"fps": 50.0, "personas_por_minuto": 3000.0, "semilla": 5},
    },
    "lineas": [
        {
            "nombre": "ENTRADA_SALA",
            "tipo": "ENTRADA",
            "a": [20, 620],
            "b": [1900, 620],
            "sentido_positivo": "IN",
        },
        {
            "nombre": "PUERTA_CONS_01",
            "tipo": "SALIDA",
            "a": [20, 400],
            "b": [1900, 400],
            "sentido_positivo": "OUT",
        },
    ],
    "cola": {"tamano_lote": 50, "intervalo_envio_s": 0.3},
    "intervalo_aforo_s": 1,
}


@pytest.fixture
def configuracion(tmp_path):
    CONFIG["cola"]["ruta"] = str(tmp_path / "cola.db")
    ruta = tmp_path / "config.yaml"
    ruta.write_text(yaml.safe_dump(CONFIG, allow_unicode=True), encoding="utf-8")
    return cargar(ruta)


def transporte(registro: list) -> httpx.MockTransport:
    def manejador(peticion: httpx.Request) -> httpx.Response:
        if peticion.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "token-de-prueba"})
        if "aforos" in peticion.url.path:
            return httpx.Response(201, json={"registrado": True})
        cuerpo = peticion.read().decode()
        registro.append(peticion.headers.get("Idempotency-Key"))
        return httpx.Response(201, json={"aceptados": cuerpo.count("ocurrido_en"), "rechazados": 0})

    return httpx.MockTransport(manejador)


def ejecutar(agente: Agente, segundos: float) -> None:
    temporizador = threading.Timer(segundos, agente.detener.set)
    temporizador.daemon = True
    temporizador.start()
    agente.ejecutar()


def test_el_agente_cuenta_y_entrega_todo_lo_medido(configuracion):
    claves: list[str] = []
    agente = Agente(configuracion)
    agente.emisor.cliente = httpx.Client(transport=transporte(claves))

    ejecutar(agente, 2.0)

    assert agente.cruces_contados > 0, "el agente no detecto ningun cruce"
    assert agente.cola.pendientes() == 0, "quedaron mediciones sin enviar"
    assert claves, "no se envio ningun lote"
    assert len(set(claves)) == len(claves), "se repitio una clave de idempotencia"


def test_sigue_midiendo_aunque_el_servidor_este_caido(configuracion):
    """La medicion perdida no se recupera; el envio atrasado si."""

    def caido(peticion: httpx.Request) -> httpx.Response:
        if peticion.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "t"})
        return httpx.Response(503)

    agente = Agente(configuracion)
    agente.emisor.cliente = httpx.Client(transport=httpx.MockTransport(caido))
    agente.emisor.reintentos = 1
    agente.cfg.dias_retencion_cola = 7

    ejecutar(agente, 2.0)

    assert agente.cruces_contados > 0
    assert agente.cola.pendientes() > 0, "los cruces debieron quedar en cola"


def test_retoma_los_pendientes_de_una_ejecucion_anterior(configuracion):
    """Un corte de energia no puede llevarse la jornada."""

    def caido(peticion: httpx.Request) -> httpx.Response:
        if peticion.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "t"})
        return httpx.Response(503)

    primero = Agente(configuracion)
    primero.emisor.cliente = httpx.Client(transport=httpx.MockTransport(caido))
    primero.emisor.reintentos = 1
    ejecutar(primero, 1.5)
    pendientes = primero.cola.pendientes()
    assert pendientes > 0

    claves: list[str] = []
    segundo = Agente(configuracion)
    segundo.emisor.cliente = httpx.Client(transport=transporte(claves))
    ejecutar(segundo, 2.0)

    assert segundo.cola.pendientes() == 0
    assert claves


# --- captura ---------------------------------------------------------------


def test_la_captura_simulada_entrega_marcas_de_tiempo():
    captura = CapturaSimulada(fps_objetivo=100)
    captura.iniciar()

    _, primero = captura.leer()
    _, segundo = captura.leer()

    assert primero is not None and segundo is not None
    assert segundo >= primero
    assert captura.cuadros_leidos == 2

    captura.detener()
    assert captura.conectada is False


def test_la_url_de_la_camara_no_expone_la_clave_en_el_registro():
    """El registro de operacion suele compartirse al pedir soporte."""
    captura = CapturaRTSP(url="rtsp://admin:clave-secreta@192.168.1.10:554/stream1")

    censurada = captura._url_censurada()

    assert "clave-secreta" not in censurada
    assert "192.168.1.10" in censurada


def test_una_url_sin_credencial_se_muestra_completa():
    captura = CapturaRTSP(url="rtsp://192.168.1.10:554/stream1")
    assert captura._url_censurada() == "rtsp://192.168.1.10:554/stream1"

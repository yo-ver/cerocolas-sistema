"""Pruebas del canal de envío decorado.

La ventaja que el patrón promete es poder verificar la política de resiliencia
sin simular un servidor. Estas pruebas la cobran: el emisor se sustituye por un
doble de prueba de tres líneas y cada decorador se examina por separado.
"""

from __future__ import annotations

from agente.canal import (
    CanalDirecto,
    ConMetricas,
    ConRegistroDetallado,
    ConReintentos,
    EnvioResultado,
    construir_canal,
)

FILAS = [{"id": 1, "linea": "ENTRADA_SALA"}, {"id": 2, "linea": "PUERTA_CONS_01"}]


class EmisorDoble:
    """Devuelve las respuestas programadas, una por llamada."""

    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = 0

    def enviar_lote(self, filas):
        self.llamadas += 1
        if self.respuestas:
            return self.respuestas.pop(0)
        return None


def sin_dormir(_segundos):
    """Sustituye a time.sleep para que la suite no espere de verdad."""


# --- normalización del resultado ------------------------------------------


def test_una_respuesta_del_servidor_se_normaliza():
    resultado = EnvioResultado.desde_respuesta(
        {"aceptados": 98, "rechazados": 2, "duplicado": False, "detalles": []}
    )
    assert resultado.exitoso
    assert resultado.aceptados == 98
    assert resultado.rechazados == 2


def test_la_ausencia_de_respuesta_se_normaliza_como_fallo():
    resultado = EnvioResultado.desde_respuesta(None)
    assert not resultado.exitoso
    assert resultado.motivo


def test_los_motivos_de_rechazo_se_conservan():
    resultado = EnvioResultado.desde_respuesta(
        {"aceptados": 0, "rechazados": 1, "detalles": [{"motivo": "Linea desconocida"}]}
    )
    assert resultado.detalles == ["Linea desconocida"]


# --- componente concreto ---------------------------------------------------


def test_el_canal_directo_hace_un_solo_intento():
    """Toda la resiliencia vive en los decoradores; éste no reintenta."""
    emisor = EmisorDoble([None])
    resultado = CanalDirecto(emisor).enviar(FILAS)

    assert not resultado.exitoso
    assert emisor.llamadas == 1


# --- decorador de reintentos ----------------------------------------------


def test_reintenta_hasta_lograrlo():
    emisor = EmisorDoble([None, None, {"aceptados": 2, "rechazados": 0}])
    canal = ConReintentos(CanalDirecto(emisor), intentos=5, dormir=sin_dormir)

    resultado = canal.enviar(FILAS)

    assert resultado.exitoso
    assert resultado.intentos == 3
    assert emisor.llamadas == 3


def test_no_reintenta_cuando_el_primero_funciona():
    emisor = EmisorDoble([{"aceptados": 2, "rechazados": 0}])
    canal = ConReintentos(CanalDirecto(emisor), intentos=5, dormir=sin_dormir)

    resultado = canal.enviar(FILAS)

    assert resultado.intentos == 1
    assert emisor.llamadas == 1


def test_desiste_tras_agotar_los_intentos():
    emisor = EmisorDoble([None, None, None])
    canal = ConReintentos(CanalDirecto(emisor), intentos=3, dormir=sin_dormir)

    resultado = canal.enviar(FILAS)

    assert not resultado.exitoso
    assert resultado.intentos == 3
    assert emisor.llamadas == 3


def test_la_espera_crece_y_tiene_tope():
    """Una red que acaba de fallar no mejora si se la golpea igual de rápido."""
    esperas = []
    emisor = EmisorDoble([None] * 8)
    canal = ConReintentos(
        CanalDirecto(emisor),
        intentos=8,
        espera_inicial=2.0,
        espera_maxima=10.0,
        dormir=esperas.append,
    )

    canal.enviar(FILAS)

    assert esperas[:3] == [2.0, 4.0, 8.0]
    assert max(esperas) == 10.0


def test_un_solo_intento_es_una_configuracion_valida():
    """Durante la calibración en campo interesa fallar rápido y ver el error."""
    emisor = EmisorDoble([None])
    canal = ConReintentos(CanalDirecto(emisor), intentos=1, dormir=sin_dormir)

    canal.enviar(FILAS)

    assert emisor.llamadas == 1


# --- decorador de métricas -------------------------------------------------


def test_las_metricas_cuentan_un_lote_una_sola_vez():
    """Aunque haya costado cuatro intentos, es un lote, no cuatro.

    Por eso las métricas envuelven al reintento y no al revés.
    """
    emisor = EmisorDoble([None, None, {"aceptados": 2, "rechazados": 0}])
    canal = ConMetricas(ConReintentos(CanalDirecto(emisor), intentos=5, dormir=sin_dormir))

    canal.enviar(FILAS)
    resumen = canal.resumen()

    assert resumen["lotes_enviados"] == 1
    assert resumen["intentos_totales"] == 3
    assert resumen["cruces_aceptados"] == 2


def test_las_metricas_registran_los_lotes_perdidos():
    emisor = EmisorDoble([None, None])
    canal = ConMetricas(ConReintentos(CanalDirecto(emisor), intentos=2, dormir=sin_dormir))

    canal.enviar(FILAS)

    assert canal.resumen()["lotes_fallidos"] == 1
    assert canal.resumen()["lotes_enviados"] == 0


def test_las_metricas_distinguen_los_duplicados():
    emisor = EmisorDoble([{"aceptados": 2, "rechazados": 0, "duplicado": True}])
    canal = ConMetricas(CanalDirecto(emisor))

    canal.enviar(FILAS)

    assert canal.resumen()["lotes_duplicados"] == 1


def test_las_metricas_acumulan_entre_lotes():
    emisor = EmisorDoble([{"aceptados": 10, "rechazados": 1}, {"aceptados": 20, "rechazados": 0}])
    canal = ConMetricas(CanalDirecto(emisor))

    canal.enviar(FILAS)
    canal.enviar(FILAS)

    resumen = canal.resumen()
    assert resumen["lotes_enviados"] == 2
    assert resumen["cruces_aceptados"] == 30
    assert resumen["cruces_rechazados"] == 1


# --- decorador de registro -------------------------------------------------


def test_el_registro_detallado_informa_del_exito(caplog):
    emisor = EmisorDoble([{"aceptados": 2, "rechazados": 0}])
    canal = ConRegistroDetallado(CanalDirecto(emisor))

    with caplog.at_level("INFO"):
        canal.enviar(FILAS)

    assert "Enviando lote" in caplog.text
    assert "entregado" in caplog.text


def test_el_registro_detallado_informa_del_fallo(caplog):
    emisor = EmisorDoble([None])
    canal = ConRegistroDetallado(CanalDirecto(emisor))

    with caplog.at_level("ERROR"):
        canal.enviar(FILAS)

    assert "no entregado" in caplog.text


# --- composición -----------------------------------------------------------


def test_el_constructor_compone_las_capas_en_el_orden_previsto():
    canal = construir_canal(EmisorDoble([]), dormir=sin_dormir)

    assert isinstance(canal, ConMetricas)
    assert isinstance(canal._envuelto, ConReintentos)
    assert isinstance(canal._envuelto._envuelto, CanalDirecto)


def test_la_capa_de_registro_se_anade_solo_si_se_pide():
    sin_registro = construir_canal(EmisorDoble([]), dormir=sin_dormir)
    con_registro = construir_canal(EmisorDoble([]), detallado=True, dormir=sin_dormir)

    assert not isinstance(sin_registro, ConRegistroDetallado)
    assert isinstance(con_registro, ConRegistroDetallado)


def test_las_metricas_pueden_omitirse():
    canal = construir_canal(EmisorDoble([]), con_metricas=False, dormir=sin_dormir)
    assert isinstance(canal, ConReintentos)


def test_la_pila_completa_entrega_y_mide():
    """La prueba de que la composición funciona como un todo."""
    emisor = EmisorDoble([None, {"aceptados": 2, "rechazados": 0}])
    canal = construir_canal(emisor, intentos=3, detallado=True, dormir=sin_dormir)

    resultado = canal.enviar(FILAS)

    assert resultado.exitoso
    assert resultado.intentos == 2
    assert canal._envuelto.resumen()["lotes_enviados"] == 1

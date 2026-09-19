"""Pruebas de los patrones de diseño aplicados en el backend.

Se prueban por su comportamiento observable —que el estimador correcto se
construya, que la cadena se detenga donde debe, que un observador defectuoso no
tumbe a los demás— y no por su estructura. Una prueba que solo comprobara que
existe una clase abstracta verificaría el diagrama, no el sistema.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta, timezone

import pytest

from app.servicios.estimadores import (
    ContextoEstimacion,
    EstimadorEspera,
    EstimadorFifoCorregido,
    EstimadorFifoEstricto,
    EstimadorVentanaMovil,
    comparar_estimadores,
    construir_estimador,
    estimadores_disponibles,
    registrar_estimador,
)
from app.servicios.eventos import (
    ContadorDeActividad,
    DetectorDeCalibracion,
    EventoLoteIngerido,
    ObservadorIngesta,
    PublicadorIngesta,
    RegistroDeOperacion,
)
from app.servicios.validacion import (
    ConfianzaMinima,
    ContextoCruce,
    DentroDelHorario,
    DireccionDebeSerCoherente,
    LineaDebeExistir,
    NoPuedeVenirDelFuturo,
    construir_cadena,
)

TZ = timezone(timedelta(hours=-5))
BASE = datetime(2026, 8, 15, 7, 0, tzinfo=TZ)


def m(minutos: float) -> datetime:
    return BASE + timedelta(minutes=minutos)


# ==========================================================================
# Strategy + Template Method + Factory Method — estimadores
# ==========================================================================


def test_el_factory_construye_cada_estimador_registrado():
    assert isinstance(construir_estimador("fifo_corregido"), EstimadorFifoCorregido)
    assert isinstance(construir_estimador("fifo_estricto"), EstimadorFifoEstricto)
    assert isinstance(construir_estimador("ventana_movil"), EstimadorVentanaMovil)


def test_sin_nombre_el_factory_devuelve_el_estimador_por_defecto():
    assert isinstance(construir_estimador(None), EstimadorFifoCorregido)
    assert isinstance(construir_estimador(""), EstimadorFifoCorregido)


def test_el_factory_tolera_espacios_y_mayusculas():
    assert isinstance(construir_estimador("  FIFO_Corregido "), EstimadorFifoCorregido)


def test_un_estimador_desconocido_enumera_los_disponibles():
    """El mensaje de error debe bastar para corregir la configuración."""
    with pytest.raises(ValueError) as excepcion:
        construir_estimador("inexistente")

    mensaje = str(excepcion.value)
    assert "inexistente" in mensaje
    assert "fifo_corregido" in mensaje


def test_el_catalogo_describe_cada_estimador():
    catalogo = estimadores_disponibles()
    assert set(catalogo) == {"fifo_corregido", "fifo_estricto", "ventana_movil"}
    assert all(descripcion for descripcion in catalogo.values())


def test_el_registro_admite_estimadores_nuevos_sin_tocar_el_factory():
    """La prueba real de un factory extensible: registrar sin editarlo."""

    @registrar_estimador
    class EstimadorDePrueba(EstimadorEspera):
        nombre = "solo_para_pruebas"
        descripcion = "Empareja la primera salida con la primera entrada"

        def emparejar(self, entradas, salidas, contexto):
            if not entradas or not salidas:
                return []
            return [(0, salidas[0], entradas[0])]

    try:
        estimador = construir_estimador("solo_para_pruebas")
        resultado = estimador.estimar(
            ContextoEstimacion(entradas=[m(0), m(10)], salidas=[m(30), m(40)])
        )
        assert resultado.esperas_min == [30.0]
    finally:
        from app.servicios import estimadores

        estimadores._REGISTRO.pop("solo_para_pruebas", None)


def test_el_metodo_plantilla_descarta_lo_implausible_en_todos_los_estimadores():
    """El descarte vive en la plantilla: ninguna subclase puede saltárselo.

    Es la razón de ser del Template Method aquí. Si cada estimador
    implementara el filtro por su cuenta, tarde o temprano uno lo omitiría y
    un cruce espurio llegaría al indicador.
    """
    contexto = ContextoEstimacion(
        entradas=[m(0), m(10)],
        salidas=[m(30), m(10_000)],
        espera_maxima_min=480,
    )

    for nombre in estimadores_disponibles():
        resultado = construir_estimador(nombre).estimar(contexto)
        assert resultado.descartados >= 1, f"{nombre} no descartó el valor imposible"
        assert all(e <= 480 for e in resultado.esperas_min)


def test_la_plantilla_ordena_los_cruces_aunque_lleguen_desordenados():
    """Los lotes del agente pueden llegar fuera de orden tras un corte de red."""
    ordenado = ContextoEstimacion(entradas=[m(0), m(10)], salidas=[m(30), m(40)])
    desordenado = ContextoEstimacion(entradas=[m(10), m(0)], salidas=[m(40), m(30)])

    estimador = EstimadorFifoEstricto()
    assert estimador.estimar(ordenado).esperas_min == estimador.estimar(desordenado).esperas_min


def test_el_estimador_corregido_recupera_la_espera_real():
    """Cuatro pacientes con un acompañante cada uno; solo ellos salen."""
    entradas = []
    for i in range(4):
        entradas.append(m(i * 10))
        entradas.append(m(i * 10 + 0.5))
    salidas = [m(i * 10 + 60) for i in range(4)]

    contexto = ContextoEstimacion(entradas=entradas, salidas=salidas, factor_correccion=1.0)

    assert EstimadorFifoCorregido().estimar(contexto).p50 == 60.0


def test_el_estimador_estricto_sobrestima_y_por_eso_se_conserva():
    """Es la línea de base contra la que se mide cuánto aporta corregir."""
    entradas = []
    for i in range(4):
        entradas.append(m(i * 10))
        entradas.append(m(i * 10 + 0.5))
    salidas = [m(i * 10 + 60) for i in range(4)]

    contexto = ContextoEstimacion(entradas=entradas, salidas=salidas, factor_correccion=1.0)

    corregido = EstimadorFifoCorregido().estimar(contexto).p50
    estricto = EstimadorFifoEstricto().estimar(contexto).p50

    assert estricto > corregido


def test_la_ventana_movil_se_comporta_como_el_corregido_con_peso_cero():
    """El peso local en cero debe reducirlo al estimador por defecto."""
    entradas = [m(i * 4) for i in range(30)]
    salidas = [m(40 + i * 6) for i in range(20)]
    contexto = ContextoEstimacion(entradas=entradas, salidas=salidas, factor_correccion=0.5)

    ventana = EstimadorVentanaMovil(peso_local=0.0).estimar(contexto)
    corregido = EstimadorFifoCorregido().estimar(contexto)

    assert ventana.esperas_min == corregido.esperas_min


def test_el_peso_local_se_acota_al_intervalo_valido():
    assert EstimadorVentanaMovil(peso_local=5).peso_local == 1.0
    assert EstimadorVentanaMovil(peso_local=-3).peso_local == 0.0


def test_la_comparacion_ejecuta_todos_los_estimadores_de_una_vez():
    """Es lo que sostiene el capítulo de validación del proyecto."""
    entradas = [m(i * 5) for i in range(20)]
    salidas = [m(45 + i * 5) for i in range(15)]

    comparacion = comparar_estimadores(
        ContextoEstimacion(entradas=entradas, salidas=salidas, factor_correccion=0.3)
    )

    assert set(comparacion) == set(estimadores_disponibles())
    for datos in comparacion.values():
        assert datos["muestras"] > 0
        assert datos["p50"] is not None


def test_un_contexto_vacio_no_rompe_a_ningun_estimador():
    contexto = ContextoEstimacion(entradas=[], salidas=[])
    for nombre in estimadores_disponibles():
        resultado = construir_estimador(nombre).estimar(contexto)
        assert resultado.esperas_min == []
        assert resultado.p50 is None


def test_mas_salidas_que_entradas_no_inventa_esperas():
    contexto = ContextoEstimacion(
        entradas=[m(0)], salidas=[m(30), m(35), m(40)], factor_correccion=0.0
    )
    resultado = EstimadorFifoCorregido().estimar(contexto)
    assert len(resultado.esperas_min) == 1


# ==========================================================================
# Chain of Responsibility — validación de cruces
# ==========================================================================


class LineaFalsa:
    def __init__(self, tipo: str = "ENTRADA"):
        self.tipo = tipo


def contexto(**cambios) -> ContextoCruce:
    base = {
        "indice": 0,
        "linea_nombre": "ENTRADA_SALA",
        "direccion": "IN",
        "ocurrido_en": BASE,
        "confianza": 0.9,
        "linea": LineaFalsa("ENTRADA"),
        "recibido_en": BASE + timedelta(minutes=1),
        "hora_apertura": time(6, 0),
        "hora_cierre": time(14, 0),
    }
    base.update(cambios)
    return ContextoCruce(**base)


def test_un_cruce_correcto_recorre_toda_la_cadena():
    assert construir_cadena().validar(contexto()) is None


def test_la_cadena_se_detiene_en_la_linea_desconocida():
    rechazo = construir_cadena().validar(contexto(linea=None, linea_nombre="FANTASMA"))

    assert rechazo is not None
    assert rechazo.regla == "linea_existe"
    assert "FANTASMA" in rechazo.motivo


def test_la_direccion_invertida_se_detecta_y_el_motivo_indica_la_correccion():
    """Es el error de instalación más frecuente: la flecha trazada al revés."""
    rechazo = construir_cadena().validar(contexto(direccion="OUT"))

    assert rechazo is not None
    assert rechazo.regla == "direccion_coherente"
    assert "ENTRADA" in rechazo.motivo and "IN" in rechazo.motivo


def test_una_marca_de_tiempo_futura_se_rechaza():
    rechazo = construir_cadena().validar(contexto(ocurrido_en=BASE + timedelta(hours=3)))

    assert rechazo is not None
    assert rechazo.regla == "sin_futuro"


def test_se_tolera_un_desfase_pequeno_de_reloj():
    """Rechazar por segundos convertiría un problema de precisión en pérdida."""
    assert (
        construir_cadena().validar(
            contexto(ocurrido_en=BASE + timedelta(minutes=3), recibido_en=BASE)
        )
        is None
    )


def test_el_orden_de_la_cadena_reporta_el_sintoma_util():
    """Un cruce con dos defectos debe reportar el que permite corregir.

    Una línea inexistente y una marca futura a la vez: importa que se reporte
    la línea, porque sin línea configurada nada más se puede diagnosticar.
    """
    rechazo = construir_cadena().validar(
        contexto(linea=None, ocurrido_en=BASE + timedelta(hours=5))
    )

    assert rechazo.regla == "linea_existe"


def test_la_confianza_minima_no_se_aplica_por_defecto():
    assert construir_cadena().validar(contexto(confianza=0.1)) is None


def test_la_confianza_minima_filtra_cuando_se_configura():
    cadena = construir_cadena(confianza_minima=0.5)

    assert cadena.validar(contexto(confianza=0.9)) is None
    rechazo = cadena.validar(contexto(confianza=0.2))
    assert rechazo is not None
    assert rechazo.regla == "confianza_minima"


def test_un_cruce_sin_confianza_pasa_el_filtro():
    """El agente puede no reportarla; ausencia no es baja confianza."""
    assert construir_cadena(confianza_minima=0.5).validar(contexto(confianza=None)) is None


def test_el_horario_no_se_exige_por_defecto():
    """Las colas empiezan antes de la apertura y contarlas es el objetivo."""
    madrugada = BASE.replace(hour=4, minute=30)
    assert (
        construir_cadena().validar(
            contexto(ocurrido_en=madrugada, recibido_en=madrugada + timedelta(minutes=1))
        )
        is None
    )


def test_el_horario_filtra_cuando_se_exige():
    cadena = construir_cadena(exigir_horario=True)
    madrugada = BASE.replace(hour=2, minute=0)

    rechazo = cadena.validar(
        contexto(ocurrido_en=madrugada, recibido_en=madrugada + timedelta(minutes=1))
    )
    assert rechazo is not None
    assert rechazo.regla == "dentro_horario"


def test_la_regla_de_horario_se_omite_si_la_sala_no_lo_declara():
    cadena = construir_cadena(exigir_horario=True)
    assert cadena.validar(contexto(hora_apertura=None, hora_cierre=None)) is None


def test_una_regla_aislada_puede_probarse_sin_la_cadena():
    """Cada eslabón es independiente: ésa es la ventaja del patrón."""
    assert LineaDebeExistir().evaluar(contexto(linea=None)) is not None
    assert DireccionDebeSerCoherente().evaluar(contexto(direccion="OUT")) is not None
    assert NoPuedeVenirDelFuturo().evaluar(contexto()) is None
    assert ConfianzaMinima(0.99).evaluar(contexto(confianza=0.5)) is not None
    assert DentroDelHorario().evaluar(contexto()) is None


def test_la_cadena_puede_armarse_a_medida():
    """Una sala nocturna necesita reglas distintas; el patrón lo permite."""
    solo_futuro = NoPuedeVenirDelFuturo()

    assert solo_futuro.validar(contexto(linea=None)) is None
    assert solo_futuro.validar(contexto(ocurrido_en=BASE + timedelta(days=1))) is not None


# ==========================================================================
# Observer — eventos de ingesta
# ==========================================================================


def evento(**cambios) -> EventoLoteIngerido:
    base = {
        "sala_codigo": "LOR-CE-01",
        "lote_id": "abc-123",
        "agente": "lorena-edge-01",
        "recibidos": 100,
        "aceptados": 98,
        "rechazados": 2,
        "duplicado": False,
        "momento": datetime.now(UTC),
        "motivos": [],
    }
    base.update(cambios)
    return EventoLoteIngerido(**base)


class ObservadorEspia(ObservadorIngesta):
    nombre = "espia"

    def __init__(self):
        self.recibidos: list[EventoLoteIngerido] = []

    def notificar(self, evento):
        self.recibidos.append(evento)


class ObservadorDefectuoso(ObservadorIngesta):
    nombre = "defectuoso"

    def notificar(self, evento):
        raise RuntimeError("fallo deliberado")


def test_el_publicador_entrega_a_todos_los_suscriptores():
    publicador = PublicadorIngesta()
    primero, segundo = ObservadorEspia(), ObservadorEspia()
    publicador.suscribir(primero)
    publicador.suscribir(segundo)

    publicador.publicar(evento())

    assert len(primero.recibidos) == 1
    assert len(segundo.recibidos) == 1


def test_suscribir_dos_veces_no_duplica_la_entrega():
    publicador = PublicadorIngesta()
    espia = ObservadorEspia()
    publicador.suscribir(espia)
    publicador.suscribir(espia)

    publicador.publicar(evento())

    assert len(espia.recibidos) == 1


def test_cancelar_la_suscripcion_detiene_la_entrega():
    publicador = PublicadorIngesta()
    espia = ObservadorEspia()
    publicador.suscribir(espia)
    publicador.cancelar(espia)

    publicador.publicar(evento())

    assert espia.recibidos == []
    assert espia.nombre not in publicador.suscriptores


def test_cancelar_a_quien_no_esta_suscrito_no_falla():
    PublicadorIngesta().cancelar(ObservadorEspia())


def test_un_observador_defectuoso_no_impide_que_los_demas_se_enteren():
    """Perder un aviso es aceptable; perder una medición no.

    Es la razón por la que el publicador aísla los fallos: la ingesta no puede
    caerse porque un observador esté mal escrito.
    """
    publicador = PublicadorIngesta()
    publicador.suscribir(ObservadorDefectuoso())
    espia = ObservadorEspia()
    publicador.suscribir(espia)

    publicador.publicar(evento())  # no debe propagar la excepción

    assert len(espia.recibidos) == 1


def test_la_tasa_de_rechazo_se_calcula_sobre_lo_recibido():
    assert evento(recibidos=100, rechazados=25).tasa_rechazo == 0.25
    assert evento(recibidos=0, rechazados=0).tasa_rechazo == 0.0


def test_el_detector_de_calibracion_avisa_ante_rechazo_masivo(caplog):
    detector = DetectorDeCalibracion(umbral=0.5, minimo_eventos=10)

    with caplog.at_level("WARNING"):
        detector.notificar(
            evento(
                recibidos=100,
                aceptados=10,
                rechazados=90,
                motivos=["La linea ENTRADA_SALA es de tipo ENTRADA"],
            )
        )

    assert "calibracion" in caplog.text.lower()
    assert "LOR-CE-01" in caplog.text


def test_el_detector_calla_ante_rechazos_normales(caplog):
    detector = DetectorDeCalibracion(umbral=0.5, minimo_eventos=10)

    with caplog.at_level("WARNING"):
        detector.notificar(evento(recibidos=100, aceptados=96, rechazados=4))

    assert caplog.text == ""


def test_el_detector_calla_con_lotes_pequenos(caplog):
    """Con pocos eventos la tasa es demasiado volátil para concluir nada."""
    detector = DetectorDeCalibracion(umbral=0.5, minimo_eventos=10)

    with caplog.at_level("WARNING"):
        detector.notificar(evento(recibidos=3, aceptados=1, rechazados=2))

    assert caplog.text == ""


def test_el_detector_ignora_los_lotes_duplicados(caplog):
    detector = DetectorDeCalibracion()
    with caplog.at_level("WARNING"):
        detector.notificar(evento(duplicado=True, recibidos=100, rechazados=100))
    assert caplog.text == ""


def test_el_contador_acumula_por_sala():
    contador = ContadorDeActividad()

    contador.notificar(evento(sala_codigo="LOR-CE-01", aceptados=98, rechazados=2))
    contador.notificar(evento(sala_codigo="LOR-CE-01", aceptados=50, rechazados=0))
    contador.notificar(evento(sala_codigo="REG-CE-01", aceptados=70, rechazados=1))

    resumen = contador.resumen()
    assert resumen["LOR-CE-01"]["lotes"] == 2
    assert resumen["LOR-CE-01"]["aceptados"] == 148
    assert resumen["REG-CE-01"]["aceptados"] == 70


def test_el_contador_no_suma_lotes_duplicados():
    """Un reenvío no aporta cruces nuevos; contarlo falsearía el total."""
    contador = ContadorDeActividad()
    contador.notificar(evento(aceptados=98))
    contador.notificar(evento(aceptados=98, duplicado=True))

    assert contador.resumen()["LOR-CE-01"]["aceptados"] == 98


def test_el_registro_de_operacion_distingue_el_duplicado(caplog):
    observador = RegistroDeOperacion()

    with caplog.at_level("INFO"):
        observador.notificar(evento())
        observador.notificar(evento(duplicado=True))

    assert "Lote ingerido" in caplog.text
    assert "duplicado" in caplog.text.lower()


def test_la_ventana_movil_tambien_se_detiene_sin_entradas_suficientes():
    """La rama de corte debe existir en todos los estimadores, no solo en uno."""
    contexto = ContextoEstimacion(
        entradas=[m(0), m(5)],
        salidas=[m(40), m(45), m(50), m(55)],
        factor_correccion=0.5,
    )
    resultado = EstimadorVentanaMovil().estimar(contexto)
    assert len(resultado.esperas_min) < 4

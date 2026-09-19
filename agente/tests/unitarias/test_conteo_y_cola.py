"""Pruebas del agente: geometria de cruces y cola local.

La logica de cruce se prueba sin camara ni modelo, que es justamente para lo
que se separo del detector.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agente.cola import ColaLocal
from agente.conteo import ContadorSala, CruceDetectado, Deteccion, LineaConteo

T0 = datetime(2026, 8, 10, 7, 0, tzinfo=UTC)


def persona(track_id: int, x: float, y_pies: float, alto: float = 180) -> Deteccion:
    """Construye una deteccion cuya base esta en (x, y_pies)."""
    return Deteccion(
        track_id=track_id,
        caja=(x - 35, y_pies - alto, x + 35, y_pies),
        confianza=0.9,
    )


def linea_horizontal(nombre: str = "ENTRADA_SALA", y: float = 500) -> LineaConteo:
    return LineaConteo(nombre=nombre, a=(100, y), b=(900, y), sentido_positivo="IN")


# --- geometria basica ------------------------------------------------------


def test_el_ancla_es_la_base_del_recuadro():
    """Una persona alta y una baja cruzan en el mismo punto del piso."""
    alta = persona(1, x=500, y_pies=520, alto=220)
    baja = persona(2, x=500, y_pies=520, alto=140)
    assert alta.ancla == baja.ancla == (500, 520)


def test_cruce_simple_hacia_adentro():
    linea = linea_horizontal()

    # Debajo de la linea (y mayor), luego encima: cruza hacia adentro.
    assert linea.procesar([persona(1, 500, 520)], T0) == []
    cruces = linea.procesar([persona(1, 500, 480)], T0 + timedelta(seconds=1))

    assert len(cruces) == 1
    assert cruces[0].direccion == "IN"
    assert cruces[0].linea == "ENTRADA_SALA"


def test_cruce_en_sentido_contrario():
    linea = linea_horizontal()
    linea.procesar([persona(1, 500, 480)], T0)
    cruces = linea.procesar([persona(1, 500, 520)], T0 + timedelta(seconds=1))

    assert len(cruces) == 1
    assert cruces[0].direccion == "OUT"


def test_no_cuenta_a_quien_no_cruza():
    linea = linea_horizontal()
    linea.procesar([persona(1, 500, 520)], T0)
    cruces = linea.procesar([persona(1, 520, 515)], T0 + timedelta(seconds=1))
    assert cruces == []


def test_primer_cuadro_nunca_genera_cruce():
    """Sin lado previo no hay cambio de lado que detectar."""
    linea = linea_horizontal()
    assert linea.procesar([persona(1, 500, 300)], T0) == []


# --- casos que rompen implementaciones ingenuas ----------------------------


def test_ignora_cruces_fuera_del_tramo():
    """La linea es un segmento, no una recta infinita.

    Alguien que camina al otro extremo de la sala esta del otro lado de la
    prolongacion de la linea, pero no ha cruzado la puerta.
    """
    linea = linea_horizontal(y=500)  # va de x=100 a x=900

    linea.procesar([persona(1, 1500, 520)], T0)
    cruces = linea.procesar([persona(1, 1500, 480)], T0 + timedelta(seconds=1))

    assert cruces == []


def test_rebote_sobre_la_linea_no_multiplica_cruces():
    """Alguien detenido justo sobre la linea oscila un pixel entre cuadros.

    Sin la espera minima esto generaria decenas de cruces alternados y
    arruinaria las curvas acumuladas.
    """
    linea = linea_horizontal()
    linea.procesar([persona(1, 500, 520)], T0)

    cruces = []
    for i in range(1, 11):
        y = 498 if i % 2 else 502
        cruces.extend(linea.procesar([persona(1, 500, y)], T0 + timedelta(milliseconds=100 * i)))

    # Solo el primer cambio de lado cuenta; el resto cae dentro de la espera.
    assert len(cruces) == 1


def test_rebote_permitido_tras_la_espera_minima():
    """Quien realmente entra y despues sale debe generar dos cruces."""
    linea = linea_horizontal()
    linea.procesar([persona(1, 500, 520)], T0)

    entrada = linea.procesar([persona(1, 500, 480)], T0 + timedelta(seconds=1))
    salida = linea.procesar([persona(1, 500, 520)], T0 + timedelta(seconds=30))

    assert len(entrada) == 1 and entrada[0].direccion == "IN"
    assert len(salida) == 1 and salida[0].direccion == "OUT"


def test_identificador_reciclado_no_hereda_estado():
    """Los seguidores reutilizan identificadores.

    Si un objeto nuevo recibe el identificador de uno viejo y hereda su lado
    previo, se contaria un cruce que nunca ocurrio.
    """
    linea = linea_horizontal()
    linea.procesar([persona(7, 500, 520)], T0)

    linea.olvidar({7})

    # El objeto 7 reaparece ya del otro lado, sin haber cruzado.
    cruces = linea.procesar([persona(7, 500, 480)], T0 + timedelta(minutes=5))
    assert cruces == []


def test_varias_personas_a_la_vez():
    linea = linea_horizontal()
    previos = [persona(i, 200 + i * 100, 520) for i in range(1, 5)]
    linea.procesar(previos, T0)

    actuales = [persona(i, 200 + i * 100, 480) for i in range(1, 5)]
    cruces = linea.procesar(actuales, T0 + timedelta(seconds=1))

    assert len(cruces) == 4
    assert {c.direccion for c in cruces} == {"IN"}


# --- contador de sala ------------------------------------------------------


def test_contador_agrupa_varias_lineas():
    contador = ContadorSala(
        [
            LineaConteo("ENTRADA_SALA", (100, 500), (900, 500), "IN"),
            LineaConteo("PUERTA_CONS_01", (100, 300), (900, 300), "OUT"),
        ]
    )

    contador.procesar([persona(1, 500, 520), persona(2, 400, 320)], T0)
    cruces = contador.procesar(
        [persona(1, 500, 480), persona(2, 400, 280)], T0 + timedelta(seconds=1)
    )

    por_linea = {c.linea: c.direccion for c in cruces}
    assert por_linea == {"ENTRADA_SALA": "IN", "PUERTA_CONS_01": "OUT"}


def test_flujo_completo_produce_curvas_coherentes():
    """Diez personas entran y luego pasan a consultorio."""
    contador = ContadorSala(
        [
            LineaConteo("ENTRADA_SALA", (0, 500), (1000, 500), "IN"),
            LineaConteo("PUERTA_CONS_01", (0, 300), (1000, 300), "OUT"),
        ]
    )

    entradas = salidas = 0
    for i in range(10):
        x = 100 + i * 80
        t = T0 + timedelta(seconds=i * 10)
        contador.procesar([persona(i, x, 520)], t)
        for cruce in contador.procesar([persona(i, x, 480)], t + timedelta(seconds=1)):
            entradas += 1 if cruce.direccion == "IN" else 0

        t2 = T0 + timedelta(minutes=30, seconds=i * 10)
        contador.procesar([persona(i, x, 320)], t2)
        for cruce in contador.procesar([persona(i, x, 280)], t2 + timedelta(seconds=1)):
            salidas += 1 if cruce.direccion == "OUT" else 0

    assert entradas == 10
    assert salidas == 10


# --- cola local ------------------------------------------------------------


@pytest.fixture
def cola(tmp_path):
    return ColaLocal(tmp_path / "cola.db")


def cruce(n: int) -> CruceDetectado:
    return CruceDetectado(
        linea="ENTRADA_SALA",
        direccion="IN",
        ocurrido_en=T0 + timedelta(seconds=n),
        confianza=0.9,
        track_id=n,
    )


def test_cola_encola_y_entrega(cola):
    cola.encolar([cruce(i) for i in range(5)])
    assert cola.pendientes() == 5

    lote = cola.tomar_lote(3)
    assert len(lote) == 3
    # Tomar no marca: si el proceso muere ahora, no se pierde nada.
    assert cola.pendientes() == 5

    cola.confirmar([fila["id"] for fila in lote])
    assert cola.pendientes() == 2


def test_cola_sobrevive_al_reinicio(tmp_path):
    ruta = tmp_path / "cola.db"
    primera = ColaLocal(ruta)
    primera.encolar([cruce(i) for i in range(7)])

    # Simula un corte de energia: se abre de nuevo el mismo archivo.
    segunda = ColaLocal(ruta)
    assert segunda.pendientes() == 7


def test_cola_respeta_el_orden(cola):
    cola.encolar([cruce(i) for i in range(10)])
    lote = cola.tomar_lote(4)
    assert [fila["track_id"] for fila in lote] == [0, 1, 2, 3]


def test_purga_solo_lo_confirmado(cola):
    cola.encolar([cruce(i) for i in range(4)])
    lote = cola.tomar_lote(2)
    cola.confirmar([fila["id"] for fila in lote])

    # Con retencion de 7 dias no se purga nada recien creado.
    assert cola.purgar_enviados(dias=7) == 0
    assert cola.purgar_enviados(dias=0) == 2
    assert cola.pendientes() == 2


def test_resumen_informa_el_mas_antiguo(cola):
    cola.encolar([cruce(i) for i in range(3)])
    resumen = cola.resumen()
    assert resumen["pendientes"] == 3
    assert resumen["mas_antiguo"] == T0.isoformat()

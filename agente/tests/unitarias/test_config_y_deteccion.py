"""Pruebas de la configuracion y del detector simulado.

La validacion de la configuracion merece pruebas propias porque sus fallos son
silenciosos: un agente con una sola linea de entrada arranca sin protestar,
mide durante toda una jornada y produce media curva, con la que no se puede
calcular ningun tiempo de espera. Detectarlo al arrancar cuesta un segundo;
detectarlo al revisar los datos cuesta una jornada de campo.
"""

from __future__ import annotations

import pytest
import yaml

from agente.config import cargar, validar
from agente.deteccion import DetectorSimulado, construir_detector

BASE = {
    "sala_id": "LOR-CE-01",
    "hostname": "lorena-edge-01",
    "api": {"url": "http://localhost:8000", "email": "a@b.pe", "password": "x"},
    "video": {"fuente": "simulada", "fps_objetivo": 10},
    "detector": {"tipo": "simulado"},
    "lineas": [
        {"nombre": "ENTRADA_SALA", "tipo": "ENTRADA", "a": [0, 500], "b": [900, 500]},
        {
            "nombre": "PUERTA_CONS_01",
            "tipo": "SALIDA",
            "a": [0, 300],
            "b": [900, 300],
            "sentido_positivo": "OUT",
        },
    ],
}


def escribir(tmp_path, datos):
    ruta = tmp_path / "config.yaml"
    ruta.write_text(yaml.safe_dump(datos, allow_unicode=True), encoding="utf-8")
    return ruta


# --- carga -----------------------------------------------------------------


def test_carga_una_configuracion_completa(tmp_path):
    cfg = cargar(escribir(tmp_path, BASE))

    assert cfg.sala_id == "LOR-CE-01"
    assert cfg.fps_objetivo == 10
    assert len(cfg.lineas) == 2
    assert cfg.lineas[0].tipo == "ENTRADA"


def test_construye_las_lineas_de_conteo(tmp_path):
    lineas = cargar(escribir(tmp_path, BASE)).construir_lineas()

    assert [linea.nombre for linea in lineas] == ["ENTRADA_SALA", "PUERTA_CONS_01"]
    assert lineas[0].a == (0.0, 500.0)
    assert lineas[1].sentido_positivo == "OUT"


def test_acepta_coordenadas_como_diccionario(tmp_path):
    """Ambas notaciones aparecen segun quien edite el archivo a mano."""
    datos = {**BASE, "lineas": [dict(BASE["lineas"][0]), dict(BASE["lineas"][1])]}
    datos["lineas"][0] = {**datos["lineas"][0], "a": {"x": 10, "y": 20}}

    cfg = cargar(escribir(tmp_path, datos))
    assert cfg.lineas[0].a == (10.0, 20.0)


def test_un_archivo_inexistente_lo_dice_con_claridad(tmp_path):
    with pytest.raises(FileNotFoundError):
        cargar(tmp_path / "no-existe.yaml")


def test_las_credenciales_del_entorno_tienen_prioridad(tmp_path, monkeypatch):
    """Las claves no deben vivir en un archivo que se copia entre maquinas."""
    monkeypatch.setenv("AGENTE_EMAIL", "desde-entorno@hospital.pe")
    monkeypatch.setenv("AGENTE_PASSWORD", "clave-del-entorno")

    cfg = cargar(escribir(tmp_path, BASE))

    assert cfg.email == "desde-entorno@hospital.pe"
    assert cfg.password == "clave-del-entorno"


# --- validacion ------------------------------------------------------------


def test_rechaza_una_configuracion_sin_linea_de_salida(tmp_path):
    """Sin ambas curvas no hay espera que calcular, solo un conteo suelto."""
    datos = {**BASE, "lineas": [BASE["lineas"][0]]}

    with pytest.raises(ValueError, match="SALIDA"):
        cargar(escribir(tmp_path, datos))


def test_rechaza_una_configuracion_sin_linea_de_entrada(tmp_path):
    datos = {**BASE, "lineas": [BASE["lineas"][1]]}

    with pytest.raises(ValueError, match="ENTRADA"):
        cargar(escribir(tmp_path, datos))


def test_rechaza_una_configuracion_sin_lineas(tmp_path):
    datos = {**BASE, "lineas": []}

    with pytest.raises(ValueError, match="no contaria nada"):
        cargar(escribir(tmp_path, datos))


def test_rechaza_nombres_de_linea_repetidos(tmp_path):
    """Dos lineas con el mismo nombre harian que el servidor las confunda."""
    repetida = {**BASE["lineas"][1], "nombre": "ENTRADA_SALA"}
    datos = {**BASE, "lineas": [BASE["lineas"][0], repetida]}

    with pytest.raises(ValueError, match="repetidos"):
        cargar(escribir(tmp_path, datos))


def test_rechaza_una_linea_de_longitud_cero(tmp_path):
    degenerada = {**BASE["lineas"][1], "a": [100, 300], "b": [100, 300]}
    datos = {**BASE, "lineas": [BASE["lineas"][0], degenerada]}

    with pytest.raises(ValueError, match="longitud cero"):
        cargar(escribir(tmp_path, datos))


def test_rechaza_rtsp_sin_url(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTE_RTSP", raising=False)
    datos = {**BASE, "video": {"fuente": "rtsp", "url": ""}}

    with pytest.raises(ValueError, match="rtsp"):
        cargar(escribir(tmp_path, datos))


def test_rechaza_la_falta_de_credenciales(tmp_path, monkeypatch):
    for variable in ("AGENTE_EMAIL", "AGENTE_PASSWORD"):
        monkeypatch.delenv(variable, raising=False)
    datos = {**BASE, "api": {"url": "http://localhost:8000"}}

    with pytest.raises(ValueError, match="credenciales"):
        cargar(escribir(tmp_path, datos))


def test_acumula_todos_los_problemas_en_un_solo_mensaje(tmp_path, monkeypatch):
    """Corregir la configuracion de a un error por arranque seria exasperante."""
    for variable in ("AGENTE_EMAIL", "AGENTE_PASSWORD"):
        monkeypatch.delenv(variable, raising=False)
    datos = {**BASE, "api": {"url": "http://localhost:8000"}, "lineas": []}

    with pytest.raises(ValueError) as excepcion:
        cargar(escribir(tmp_path, datos))

    mensaje = str(excepcion.value)
    assert "credenciales" in mensaje
    assert "lineas configuradas" in mensaje


def test_validar_acepta_una_configuracion_correcta(tmp_path):
    cfg = cargar(escribir(tmp_path, BASE))
    validar(cfg)  # no debe levantar


# --- detector simulado -----------------------------------------------------


def test_el_detector_simulado_produce_personas():
    detector = DetectorSimulado(personas_por_minuto=600, fps=10, semilla=7)

    total = 0
    for _ in range(120):
        total += len(detector.detectar(None))

    assert total > 0


def test_las_detecciones_simuladas_son_recuadros_validos():
    detector = DetectorSimulado(personas_por_minuto=600, fps=10, semilla=7)

    for _ in range(80):
        for deteccion in detector.detectar(None):
            x1, y1, x2, y2 = deteccion.caja
            assert x2 > x1 and y2 > y1
            assert 0 <= deteccion.confianza <= 1
            assert deteccion.ancla == ((x1 + x2) / 2, y2)


def test_las_personas_simuladas_avanzan_hacia_arriba():
    """Suben en la imagen: hacia la sala o hacia los consultorios."""
    detector = DetectorSimulado(personas_por_minuto=600, fps=10, semilla=3)

    posiciones: dict[int, list[float]] = {}
    for _ in range(60):
        for deteccion in detector.detectar(None):
            posiciones.setdefault(deteccion.track_id, []).append(deteccion.ancla[1])

    trayectorias = [valores for valores in posiciones.values() if len(valores) > 3]
    assert trayectorias
    assert all(valores[-1] < valores[0] for valores in trayectorias)


def test_la_semilla_hace_reproducible_la_simulacion():
    def contar(semilla):
        detector = DetectorSimulado(personas_por_minuto=400, fps=10, semilla=semilla)
        return [len(detector.detectar(None)) for _ in range(50)]

    assert contar(11) == contar(11)


def test_construye_el_detector_simulado_desde_la_configuracion():
    detector = construir_detector({"tipo": "simulado", "simulado": {"fps": 5.0}})
    assert isinstance(detector, DetectorSimulado)
    assert detector.fps == 5.0


def test_rechaza_un_tipo_de_detector_desconocido():
    with pytest.raises(ValueError, match="desconocido"):
        construir_detector({"tipo": "inexistente"})


def test_el_detector_yolo_explica_como_instalarlo():
    """Si falta la dependencia, el mensaje debe indicar la salida.

    Un ImportError crudo en el equipo de borde de un hospital, a las cinco de
    la manana, no le sirve a nadie.
    """
    try:
        import ultralytics  # noqa: F401
    except ImportError:
        from agente.deteccion import DetectorYolo

        with pytest.raises(RuntimeError, match="ultralytics"):
            DetectorYolo()
    else:  # pragma: no cover - solo si la dependencia esta instalada
        pytest.skip("ultralytics esta instalado en este entorno")

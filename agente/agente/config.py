"""Carga de la configuracion del agente.

La configuracion vive en un archivo YAML porque la escribe una persona durante
la visita tecnica, mirando el video para trazar las lineas. Las credenciales,
en cambio, se leen de variables de entorno: no deben quedar en un archivo que
se copia entre maquinas o se sube a un repositorio.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from agente.conteo import LineaConteo


@dataclass
class ConfiguracionLinea:
    nombre: str
    tipo: str  # ENTRADA o SALIDA
    a: tuple[float, float]
    b: tuple[float, float]
    sentido_positivo: str = "IN"


@dataclass
class Configuracion:
    sala_id: str
    hostname: str
    url_api: str
    email: str
    password: str

    fuente: str = "simulada"  # rtsp | archivo | simulada
    url_rtsp: str = ""
    fps_objetivo: float = 10.0
    ancho_proceso: int | None = 960

    detector: dict = field(default_factory=lambda: {"tipo": "simulado"})
    lineas: list[ConfiguracionLinea] = field(default_factory=list)

    ruta_cola: str = "./datos/cola.db"
    tamano_lote: int = 200
    intervalo_envio_s: float = 15.0
    intervalo_aforo_s: float = 300.0
    dias_retencion_cola: int = 7

    nivel_registro: str = "INFO"

    def construir_lineas(self) -> list[LineaConteo]:
        return [
            LineaConteo(
                nombre=linea.nombre,
                a=linea.a,
                b=linea.b,
                sentido_positivo=linea.sentido_positivo,
            )
            for linea in self.lineas
        ]


def _par(valor) -> tuple[float, float]:
    if isinstance(valor, dict):
        return (float(valor["x"]), float(valor["y"]))
    return (float(valor[0]), float(valor[1]))


def cargar(ruta: str | Path) -> Configuracion:
    ruta = Path(ruta)
    if not ruta.exists():
        raise FileNotFoundError(f"No existe el archivo de configuracion: {ruta}")

    datos = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}

    lineas = [
        ConfiguracionLinea(
            nombre=item["nombre"],
            tipo=item.get("tipo", "ENTRADA").upper(),
            a=_par(item["a"]),
            b=_par(item["b"]),
            sentido_positivo=item.get("sentido_positivo", "IN").upper(),
        )
        for item in datos.get("lineas", [])
    ]

    api = datos.get("api", {})
    video = datos.get("video", {})
    cola = datos.get("cola", {})

    configuracion = Configuracion(
        sala_id=datos["sala_id"],
        hostname=datos.get("hostname", os.uname().nodename),
        url_api=api.get("url", "http://localhost:8000"),
        # Las credenciales salen del entorno; el YAML solo declara el valor por
        # defecto para desarrollo.
        email=os.environ.get("AGENTE_EMAIL", api.get("email", "")),
        password=os.environ.get("AGENTE_PASSWORD", api.get("password", "")),
        fuente=video.get("fuente", "simulada"),
        url_rtsp=os.environ.get("AGENTE_RTSP", video.get("url", "")),
        fps_objetivo=float(video.get("fps_objetivo", 10)),
        ancho_proceso=video.get("ancho_proceso", 960),
        detector=datos.get("detector", {"tipo": "simulado"}),
        lineas=lineas,
        ruta_cola=cola.get("ruta", "./datos/cola.db"),
        tamano_lote=int(cola.get("tamano_lote", 200)),
        intervalo_envio_s=float(cola.get("intervalo_envio_s", 15)),
        intervalo_aforo_s=float(datos.get("intervalo_aforo_s", 300)),
        dias_retencion_cola=int(cola.get("dias_retencion", 7)),
        nivel_registro=datos.get("nivel_registro", "INFO"),
    )

    validar(configuracion)
    return configuracion


def validar(configuracion: Configuracion) -> None:
    problemas: list[str] = []

    if not configuracion.sala_id:
        problemas.append("Falta sala_id")
    if not configuracion.lineas:
        problemas.append("No hay lineas configuradas: el agente no contaria nada")
    if not configuracion.email or not configuracion.password:
        problemas.append(
            "Faltan credenciales. Definir AGENTE_EMAIL y AGENTE_PASSWORD en el entorno"
        )
    if configuracion.fuente == "rtsp" and not configuracion.url_rtsp:
        problemas.append("La fuente es rtsp pero no se indico la URL")

    entradas = [linea for linea in configuracion.lineas if linea.tipo == "ENTRADA"]
    salidas = [linea for linea in configuracion.lineas if linea.tipo == "SALIDA"]

    # Sin una de las dos curvas no hay tiempo de espera que calcular, solo un
    # conteo suelto. Conviene detectarlo al arrancar y no al revisar los datos.
    if not entradas:
        problemas.append("No hay ninguna linea de tipo ENTRADA")
    if not salidas:
        problemas.append("No hay ninguna linea de tipo SALIDA")

    nombres = [linea.nombre for linea in configuracion.lineas]
    if len(nombres) != len(set(nombres)):
        problemas.append("Hay nombres de linea repetidos")

    for linea in configuracion.lineas:
        if linea.a == linea.b:
            problemas.append(f"La linea {linea.nombre} tiene longitud cero")

    if problemas:
        raise ValueError("Configuracion invalida:\n  - " + "\n  - ".join(problemas))

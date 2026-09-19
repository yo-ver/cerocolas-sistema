#!/usr/bin/env python3
"""Ajusta el factor de correccion de cada sala minimizando el sesgo.

En campo el insumo son las observaciones manuales con cronometro cargadas en
/v1/calibraciones. Aqui, para desarrollo, se usa la verdad de terreno
sintetica, que cumple el mismo papel.

Por que hace falta este ajuste: el factor de correccion no es un conteo de
acompanantes sino un parametro efectivo que absorbe tambien el abandono al
cierre, el personal que cruza la linea y el efecto de las atenciones por
prioridad. Dos salas con la misma proporcion real de acompanantes pueden
requerir factores distintos segun su nivel de saturacion.

Criterio: se busca el factor que anula el SESGO, no el que minimiza el error
absoluto medio. Un sesgo cercano a cero significa que el sistema no se
equivoca sistematicamente en una direccion; el error residual refleja la
variabilidad entre jornadas, que ningun parametro puede eliminar.

Uso:
    python -m scripts.ajustar_ratio                 # solo informa
    python -m scripts.ajustar_ratio --aplicar       # guarda en la base
"""

from __future__ import annotations

import argparse
import csv
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from sqlalchemy import select

from app.db import SesionLocal
from app.models import SalaEspera
from app.servicios import curvas


def cargar_jornadas(carpeta: Path, sala: str):
    cruces: dict[str, tuple[list, list]] = defaultdict(lambda: ([], []))
    ruta_cruces = carpeta / f"cruces_{sala}.csv"
    with ruta_cruces.open(encoding="utf-8") as f:
        for fila in csv.DictReader(f):
            momento = datetime.fromisoformat(fila["ocurrido_en"])
            fecha = momento.date().isoformat()
            destino = cruces[fecha][0] if fila["direccion"] == "IN" else cruces[fecha][1]
            destino.append(momento)

    verdad: dict[str, list[float]] = defaultdict(list)
    ruta_verdad = carpeta / f"verdad_terreno_{sala}.csv"
    with ruta_verdad.open(encoding="utf-8") as f:
        for fila in csv.DictReader(f):
            if fila["espera_min"]:
                verdad[fila["fecha"]].append(float(fila["espera_min"]))

    return cruces, verdad


def evaluar(cruces, verdad, factor: float) -> tuple[float, float]:
    errores = []
    for fecha in sorted(verdad):
        entradas, salidas = cruces[fecha]
        resultado = curvas.calcular(entradas, salidas, ratio_acompanante=factor)
        if resultado.p50 is None:
            continue
        real = curvas.percentil(verdad[fecha], 0.50)
        errores.append(resultado.p50 - real)
    if not errores:
        return float("nan"), float("nan")
    return st.mean(errores), st.mean(abs(e) for e in errores)


def buscar(cruces, verdad) -> tuple[float, float, float]:
    """Busqueda ternaria sobre |sesgo|, que es monotona decreciente en r."""
    bajo, alto = 0.0, 1.0
    for _ in range(40):
        a = bajo + (alto - bajo) / 3
        b = alto - (alto - bajo) / 3
        if abs(evaluar(cruces, verdad, a)[0]) < abs(evaluar(cruces, verdad, b)[0]):
            alto = b
        else:
            bajo = a
    optimo = round((bajo + alto) / 2, 3)
    sesgo, eam = evaluar(cruces, verdad, optimo)
    return optimo, sesgo, eam


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datos", default=str(RAIZ.parent / "datos_sinteticos"))
    parser.add_argument(
        "--aplicar", action="store_true", help="Guarda el factor ajustado en la base de datos"
    )
    args = parser.parse_args()

    carpeta = Path(args.datos)
    sesion = SesionLocal()

    try:
        for sala in sesion.scalars(select(SalaEspera).order_by(SalaEspera.codigo)):
            if not (carpeta / f"cruces_{sala.codigo}.csv").exists():
                continue

            cruces, verdad = cargar_jornadas(carpeta, sala.codigo)
            actual = float(sala.ratio_acompanante or 0)
            sesgo_actual, eam_actual = evaluar(cruces, verdad, actual)
            optimo, sesgo_optimo, eam_optimo = buscar(cruces, verdad)

            print(f"--- {sala.codigo} — {sala.nombre} ---")
            print(
                f"  factor actual  : {actual:.3f}  "
                f"sesgo {sesgo_actual:+6.1f} min   EAM {eam_actual:5.1f} min"
            )
            print(
                f"  factor ajustado: {optimo:.3f}  "
                f"sesgo {sesgo_optimo:+6.1f} min   EAM {eam_optimo:5.1f} min"
            )

            if args.aplicar:
                sala.ratio_acompanante = optimo
                print("  aplicado en la base de datos")
            print()

        if args.aplicar:
            sesion.commit()
            print("Factores guardados. Los indicadores se recalculan al consultarse:")
            print("no hay nada que reprocesar porque las esperas se derivan de los cruces.")
        else:
            print("Ejecutar con --aplicar para guardar los factores ajustados.")
    finally:
        sesion.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Valida el estimador de la API contra la verdad de terreno sintetica.

Es el ensayo del informe de validacion que se hara en campo. La diferencia es
que aqui la verdad se conoce con exactitud, lo que permite medir el error del
METODO por separado del error del DETECTOR. En campo ambos se mezclan.

Compara, para cada jornada:
  - P50 y P90 reales, tomados de verdad_terreno_*.csv
  - P50 y P90 que devuelve la API en /v1/indicadores

Y reporta error absoluto medio y sesgo.

ADVERTENCIA: opera sobre datos ficticios. El resultado acota el error del
metodo de curvas acumuladas, no el del sistema completo instalado.

Uso:
    python -m scripts.validar --api http://localhost:8000 --ratio 0.34
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))


def percentil(valores: list[float], q: float) -> float | None:
    if not valores:
        return None
    v = sorted(valores)
    if len(v) == 1:
        return v[0]
    pos = (len(v) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return v[int(lo)]
    return v[int(lo)] + (v[int(hi)] - v[int(lo)]) * (pos - lo)


def cargar_verdad(archivo: Path) -> dict[str, list[float]]:
    """Esperas reales agrupadas por fecha operativa."""
    por_fecha: dict[str, list[float]] = defaultdict(list)
    with archivo.open(encoding="utf-8") as f:
        for fila in csv.DictReader(f):
            if fila["espera_min"]:
                por_fecha[fila["fecha"]].append(float(fila["espera_min"]))
    return por_fecha


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--datos", default=str(RAIZ.parent / "datos_sinteticos"))
    parser.add_argument("--email", default="visor@cerocolas.local")
    parser.add_argument("--password", default="cambiar-visor")
    parser.add_argument(
        "--ratio",
        type=float,
        default=None,
        help="Si se indica, se usa para informar el valor configurado",
    )
    args = parser.parse_args()

    carpeta = Path(args.datos)
    archivos = sorted(carpeta.glob("verdad_terreno_*.csv"))
    if not archivos:
        print(f"No se encontro verdad_terreno_*.csv en {carpeta}")
        sys.exit(1)

    print("ADVERTENCIA: validacion sobre DATOS FICTICIOS.")
    print("Mide el error del metodo de curvas, no el del sistema instalado.\n")

    with httpx.Client(timeout=120) as cliente:
        respuesta = cliente.post(
            f"{args.api}/v1/auth/token",
            json={"email": args.email, "password": args.password},
        )
        respuesta.raise_for_status()
        cabeceras = {"Authorization": f"Bearer {respuesta.json()['access_token']}"}

        salas = {
            s["codigo"]: s
            for s in cliente.get(f"{args.api}/v1/catalogos/salas", headers=cabeceras).json()
        }

        for archivo in archivos:
            sala = archivo.stem.replace("verdad_terreno_", "")
            verdad = cargar_verdad(archivo)
            if not verdad:
                continue

            fechas = sorted(verdad)
            datos = cliente.get(
                f"{args.api}/v1/indicadores?sala={sala}&desde={fechas[0]}&hasta={fechas[-1]}",
                headers=cabeceras,
            ).json()

            estimado = {f["fecha_operativa"]: f for f in datos["filas"]}
            ratio = salas.get(sala, {}).get("ratio_acompanante", 0)

            errores_p50: list[float] = []
            errores_p90: list[float] = []
            reales_todos: list[float] = []

            for fecha in fechas:
                fila = estimado.get(fecha)
                if not fila or fila["espera_p50_min"] is None:
                    continue
                p50_real = percentil(verdad[fecha], 0.50)
                p90_real = percentil(verdad[fecha], 0.90)
                errores_p50.append(fila["espera_p50_min"] - p50_real)
                errores_p90.append(fila["espera_p90_min"] - p90_real)
                reales_todos.extend(verdad[fecha])

            if not errores_p50:
                print(f"{sala}: sin datos comparables. Cargue los cruces primero.")
                continue

            print(f"--- {sala} — {salas.get(sala, {}).get('establecimiento', '')} ---")
            print(f"  ratio_acompanante configurado : {ratio}")
            print(f"  jornadas comparadas           : {len(errores_p50)}")
            print(f"  P50 real (global)             : {percentil(reales_todos, 0.50):.1f} min")
            print(f"  P50 estimado (global)         : {datos['resumen_p50_min']:.1f} min")
            print(f"  P90 real (global)             : {percentil(reales_todos, 0.90):.1f} min")
            print(f"  P90 estimado (global)         : {datos['resumen_p90_min']:.1f} min")
            print(
                f"  EAM del P50 diario            : {st.mean(abs(e) for e in errores_p50):.1f} min"
            )
            print(f"  Sesgo del P50 diario          : {st.mean(errores_p50):+.1f} min")
            print(
                f"  EAM del P90 diario            : {st.mean(abs(e) for e in errores_p90):.1f} min"
            )
            print(f"  Sesgo del P90 diario          : {st.mean(errores_p90):+.1f} min")
            print()

    print("El sesgo importa mas que el EAM: un sesgo sistematico indica un")
    print("parametro mal calibrado (tipicamente el ratio de acompanantes),")
    print("mientras que el EAM refleja la variabilidad natural entre jornadas.")


if __name__ == "__main__":
    main()

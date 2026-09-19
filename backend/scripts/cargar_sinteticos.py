#!/usr/bin/env python3
"""Carga los datos sinteticos contra la API, simulando el agente de vision.

Envia los cruces por lotes con cabecera Idempotency-Key, exactamente como lo
hara el agente real. Permite probar de extremo a extremo la ingesta, el motor
de curvas y los indicadores antes de instalar una sola camara.

ADVERTENCIA: los datos cargados son ficticios. No deben presentarse como
mediciones ni mezclarse con la base de KoboCollect.

Uso:
    python -m scripts.cargar_sinteticos --api http://localhost:8000 \\
        --datos ../datos_sinteticos --lote 500

    # Para probar la deduplicacion, reenviando los mismos lotes:
    python -m scripts.cargar_sinteticos --reintentar
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))


def clave_idempotencia(sala: str, indice: int, cruces: list[dict]) -> str:
    """Clave determinista: el mismo lote produce siempre la misma clave.

    Asi un reintento tras una caida de red no genera cruces duplicados, y el
    script puede ejecutarse varias veces sin ensuciar la base.
    """
    firma = f"{sala}|{indice}|{cruces[0]['ocurrido_en']}|{cruces[-1]['ocurrido_en']}|{len(cruces)}"
    return hashlib.sha256(firma.encode()).hexdigest()[:40]


def autenticar(cliente: httpx.Client, api: str, email: str, password: str) -> str:
    respuesta = cliente.post(
        f"{api}/v1/auth/token", json={"email": email, "password": password}, timeout=30
    )
    respuesta.raise_for_status()
    return respuesta.json()["access_token"]


def cargar_archivo(
    cliente: httpx.Client,
    api: str,
    token: str,
    archivo: Path,
    tam_lote: int,
    agente: str | None,
) -> dict:
    with archivo.open(encoding="utf-8") as f:
        filas = list(csv.DictReader(f))

    if not filas:
        return {"archivo": archivo.name, "enviados": 0}

    sala = filas[0]["sala_id"]
    total_aceptados = total_rechazados = total_duplicados = 0

    for i in range(0, len(filas), tam_lote):
        bloque = filas[i : i + tam_lote]
        cruces = [
            {
                "linea": fila["linea"],
                "direccion": fila["direccion"],
                "ocurrido_en": fila["ocurrido_en"],
                "confianza": float(fila["confianza"]) if fila.get("confianza") else None,
                "track_id_local": int(fila["track_id_local"])
                if fila.get("track_id_local")
                else None,
            }
            for fila in bloque
        ]

        respuesta = cliente.post(
            f"{api}/v1/cruces:lote",
            json={"sala_id": sala, "agente": agente, "cruces": cruces},
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": clave_idempotencia(sala, i, cruces),
            },
            timeout=120,
        )
        if respuesta.status_code >= 400:
            print(f"  error HTTP {respuesta.status_code}: {respuesta.text[:300]}")
            respuesta.raise_for_status()

        datos = respuesta.json()
        total_aceptados += datos["aceptados"]
        total_rechazados += datos["rechazados"]
        total_duplicados += 1 if datos.get("duplicado") else 0

        if datos["rechazados"] and datos.get("detalles"):
            print(
                f"  lote {i // tam_lote}: {datos['rechazados']} rechazados. "
                f"Primer motivo: {datos['detalles'][0]['motivo']}"
            )

        print(f"  {sala}: {min(i + tam_lote, len(filas)):>6}/{len(filas)} cruces", end="\r")

    print(" " * 60, end="\r")
    return {
        "archivo": archivo.name,
        "sala": sala,
        "filas": len(filas),
        "aceptados": total_aceptados,
        "rechazados": total_rechazados,
        "lotes_duplicados": total_duplicados,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--datos", default=str(RAIZ.parent / "datos_sinteticos"))
    parser.add_argument("--lote", type=int, default=500)
    parser.add_argument("--email", default="agente@cerocolas.local")
    parser.add_argument("--password", default="cambiar-agente")
    parser.add_argument(
        "--reintentar",
        action="store_true",
        help="Reenvia los mismos lotes para comprobar la deduplicacion",
    )
    args = parser.parse_args()

    carpeta = Path(args.datos)
    archivos = sorted(carpeta.glob("cruces_*.csv"))
    if not archivos:
        print(f"No se encontraron archivos cruces_*.csv en {carpeta}")
        sys.exit(1)

    print("ADVERTENCIA: se cargaran DATOS FICTICIOS, solo para desarrollo.\n")

    with httpx.Client() as cliente:
        token = autenticar(cliente, args.api, args.email, args.password)
        agentes = {"LOR-CE-01": "lorena-edge-01", "REG-CE-01": "regional-edge-01"}

        pasadas = 2 if args.reintentar else 1
        for pasada in range(pasadas):
            if pasada == 1:
                print("\nSegunda pasada: comprobando idempotencia\n")
            for archivo in archivos:
                sala = archivo.stem.replace("cruces_", "")
                resumen = cargar_archivo(
                    cliente, args.api, token, archivo, args.lote, agentes.get(sala)
                )
                print(
                    f"  {resumen['archivo']}: {resumen['filas']} filas, "
                    f"{resumen['aceptados']} aceptados, "
                    f"{resumen['rechazados']} rechazados, "
                    f"{resumen['lotes_duplicados']} lotes ya procesados"
                )

    print("\nCarga completada.")


if __name__ == "__main__":
    main()

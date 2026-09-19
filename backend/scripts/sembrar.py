#!/usr/bin/env python3
"""Siembra el catalogo institucional y los usuarios iniciales.

Crea los dos establecimientos, sus salas de espera, las camaras y las lineas
virtuales, mas un usuario por rol. Es idempotente: se puede ejecutar varias
veces sin duplicar.

Uso:
    python -m scripts.sembrar
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db import Base, SesionLocal, motor
from app.models import (
    Agente,
    Camara,
    Establecimiento,
    LineaVirtual,
    SalaEspera,
    Usuario,
)
from app.security import hashear_password

# Los codigos RENIPRESS deben verificarse contra el registro oficial antes del
# despliegue. Los valores aqui son marcadores para desarrollo.
ESTABLECIMIENTOS = [
    {
        "codigo_unico": "00002518",
        "nombre": "Hospital Antonio Lorena",
        "categoria": "III-1",
        "provincia": "CUSCO",
        "sala": {
            "codigo": "LOR-CE-01",
            "nombre": "Sala de espera de consultorios externos",
            "aforo_referencial": 120,
            "consultorios": 6,
        },
        "agente": "lorena-edge-01",
    },
    {
        "codigo_unico": "00002519",
        "nombre": "Hospital Regional del Cusco",
        "categoria": "III-1",
        "provincia": "CUSCO",
        "sala": {
            "codigo": "REG-CE-01",
            "nombre": "Sala de espera de consultorios externos",
            "aforo_referencial": 150,
            "consultorios": 10,
        },
        "agente": "regional-edge-01",
    },
]

USUARIOS = [
    ("admin@cerocolas.local", "cambiar-admin", "ADMIN", "Administrador del sistema"),
    ("gestor@cerocolas.local", "cambiar-gestor", "GESTOR", "Jefatura de consulta externa"),
    ("visor@cerocolas.local", "cambiar-visor", "VISOR", "Consulta de indicadores"),
    ("agente@cerocolas.local", "cambiar-agente", "AGENTE", "Credencial de agentes de vision"),
]


def main() -> None:
    Base.metadata.create_all(motor)
    sesion = SesionLocal()

    try:
        primer_establecimiento = None

        for datos in ESTABLECIMIENTOS:
            est = sesion.scalar(
                select(Establecimiento).where(Establecimiento.codigo_unico == datos["codigo_unico"])
            )
            if est is None:
                est = Establecimiento(
                    codigo_unico=datos["codigo_unico"],
                    nombre=datos["nombre"],
                    categoria=datos["categoria"],
                    provincia=datos["provincia"],
                )
                sesion.add(est)
                sesion.flush()
                print(f"  establecimiento creado: {est.nombre}")
            primer_establecimiento = primer_establecimiento or est

            cfg = datos["sala"]
            sala = sesion.scalar(select(SalaEspera).where(SalaEspera.codigo == cfg["codigo"]))
            if sala is None:
                sala = SalaEspera(
                    establecimiento_id=est.id,
                    codigo=cfg["codigo"],
                    nombre=cfg["nombre"],
                    aforo_referencial=cfg["aforo_referencial"],
                    # Se deja en cero a proposito: el valor real sale de la
                    # calibracion manual, no de un supuesto.
                    ratio_acompanante=0,
                )
                sesion.add(sala)
                sesion.flush()
                print(f"  sala creada: {sala.codigo}")

            # Camara 1: entrada a la sala. Camara 2: puertas de consultorio.
            for sufijo, descripcion in (("CAM01", "entrada"), ("CAM02", "consultorios")):
                codigo_cam = f"{cfg['codigo']}-{sufijo}"
                cam = sesion.scalar(select(Camara).where(Camara.codigo == codigo_cam))
                if cam is None:
                    cam = Camara(
                        sala_id=sala.id,
                        codigo=codigo_cam,
                        url_rtsp=(
                            "rtsp://usuario:clave@192.168.1."
                            f"{10 if sufijo == 'CAM01' else 11}:554/stream1"
                        ),
                        resolucion="1920x1080",
                        fps_objetivo=10,
                    )
                    sesion.add(cam)
                    sesion.flush()
                    print(f"  camara creada: {cam.codigo} ({descripcion})")

            cam_entrada = sesion.scalar(
                select(Camara).where(Camara.codigo == f"{cfg['codigo']}-CAM01")
            )
            cam_salida = sesion.scalar(
                select(Camara).where(Camara.codigo == f"{cfg['codigo']}-CAM02")
            )

            existentes = {
                linea.nombre
                for linea in sesion.scalars(
                    select(LineaVirtual).where(LineaVirtual.sala_id == sala.id)
                )
            }

            if "ENTRADA_SALA" not in existentes:
                sesion.add(
                    LineaVirtual(
                        camara_id=cam_entrada.id,
                        sala_id=sala.id,
                        tipo="ENTRADA",
                        nombre="ENTRADA_SALA",
                        geometria={"x1": 120, "y1": 620, "x2": 1800, "y2": 620},
                        sentido_positivo="IN",
                    )
                )
                print("  linea creada: ENTRADA_SALA")

            # Una linea por puerta de consultorio: fragmenta la cola en
            # subcolas mas homogeneas y acerca el supuesto de orden de llegada.
            for n in range(1, cfg["consultorios"] + 1):
                nombre = f"PUERTA_CONS_{n:02d}"
                if nombre in existentes:
                    continue
                sesion.add(
                    LineaVirtual(
                        camara_id=cam_salida.id,
                        sala_id=sala.id,
                        tipo="SALIDA",
                        nombre=nombre,
                        consultorio_ref=f"CONS-{n:02d}",
                        geometria={"x1": 100 + n * 150, "y1": 400, "x2": 220 + n * 150, "y2": 400},
                        sentido_positivo="OUT",
                    )
                )
            print(f"  lineas de salida: {cfg['consultorios']} consultorios")

            if sesion.scalar(select(Agente).where(Agente.hostname == datos["agente"])) is None:
                sesion.add(
                    Agente(
                        establecimiento_id=est.id,
                        hostname=datos["agente"],
                        version_modelo="yolo11n-coco",
                        estado="DESCONECTADO",
                    )
                )
                print(f"  agente registrado: {datos['agente']}")

        for email, password, rol, nombre in USUARIOS:
            if sesion.scalar(select(Usuario).where(Usuario.email == email)) is None:
                sesion.add(
                    Usuario(
                        establecimiento_id=primer_establecimiento.id if rol != "ADMIN" else None,
                        email=email,
                        hash_password=hashear_password(password),
                        nombre=nombre,
                        rol=rol,
                    )
                )
                print(f"  usuario creado: {email} ({rol})")

        sesion.commit()
        print("\nSiembra completada.")
        print(
            "ADVERTENCIA: las claves por defecto son de desarrollo. "
            "Deben cambiarse antes de cualquier despliegue."
        )
    finally:
        sesion.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Traza las lineas virtuales sobre un cuadro del video.

Es la herramienta que se usa en la visita tecnica, con el video ya encuadrado.
Sin ella habria que adivinar coordenadas de pixel a mano, que es la forma mas
segura de terminar con lineas mal puestas y datos inservibles.

Dibuja la flecha A->B y rotula de que lado cae cada direccion, para que la
decision se tome viendo y no razonando sobre signos: en coordenadas de imagen
el eje vertical crece hacia abajo y la intuicion algebraica se invierte.

Uso:
    python herramientas/calibrar_lineas.py --fuente rtsp://usuario:clave@ip:554/stream1
    python herramientas/calibrar_lineas.py --fuente muestra.mp4 --salida lineas.yaml

Controles:
    clic izquierdo   marca el punto A y luego el punto B
    i                alterna la direccion positiva entre IN y OUT
    t                alterna el tipo entre ENTRADA y SALIDA
    n                confirma la linea y pide su nombre en la terminal
    z                deshace la ultima linea
    espacio          avanza un cuadro (util si hay gente tapando la puerta)
    g                guarda y sale
    q                sale sin guardar
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

VERDE = (80, 200, 120)
MORADO = (183, 74, 83)
GRIS = (150, 150, 150)
BLANCO = (255, 255, 255)


class Calibrador:
    def __init__(self, fuente: str, sala_id: str):
        import cv2

        self.cv2 = cv2
        self.fuente = fuente
        self.sala_id = sala_id
        self.captura = cv2.VideoCapture(fuente if not fuente.isdigit() else int(fuente))
        if not self.captura.isOpened():
            raise RuntimeError(f"No se pudo abrir la fuente: {fuente}")

        leido, self.cuadro = self.captura.read()
        if not leido:
            raise RuntimeError("No se pudo leer un cuadro de la fuente")

        self.lineas: list[dict] = []
        self.punto_a: tuple[int, int] | None = None
        self.cursor: tuple[int, int] = (0, 0)
        self.sentido = "IN"
        self.tipo = "ENTRADA"

    # --- interaccion ------------------------------------------------------

    def _al_hacer_clic(self, evento, x, y, _banderas, _param):
        if evento == self.cv2.EVENT_MOUSEMOVE:
            self.cursor = (x, y)
        elif evento == self.cv2.EVENT_LBUTTONDOWN:
            if self.punto_a is None:
                self.punto_a = (x, y)
            else:
                self._agregar_linea(self.punto_a, (x, y))
                self.punto_a = None

    def _agregar_linea(self, a, b) -> None:
        indice = len(self.lineas) + 1
        nombre = "ENTRADA_SALA" if self.tipo == "ENTRADA" else f"PUERTA_CONS_{indice:02d}"
        self.lineas.append(
            {
                "nombre": nombre,
                "tipo": self.tipo,
                "a": [int(a[0]), int(a[1])],
                "b": [int(b[0]), int(b[1])],
                "sentido_positivo": self.sentido,
            }
        )
        print(f"  linea agregada: {nombre} ({self.tipo}, positivo={self.sentido})")

    # --- dibujo -----------------------------------------------------------

    def _lado_izquierdo(self, a, b) -> tuple[int, int]:
        """Punto de rotulo del lado izquierdo de la flecha A->B en pantalla."""
        medio = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        dx, dy = b[0] - a[0], b[1] - a[1]
        longitud = max((dx * dx + dy * dy) ** 0.5, 1e-6)
        # Normal que apunta a la izquierda en pantalla (eje y invertido).
        nx, ny = dy / longitud, -dx / longitud
        return (int(medio[0] + nx * 45), int(medio[1] + ny * 45))

    def _dibujar_linea(self, lienzo, linea: dict) -> None:
        cv2 = self.cv2
        a = tuple(linea["a"])
        b = tuple(linea["b"])
        color = VERDE if linea["tipo"] == "ENTRADA" else MORADO

        cv2.arrowedLine(lienzo, a, b, color, 3, tipLength=0.04)
        cv2.circle(lienzo, a, 6, color, -1)

        etiqueta_izq = linea["sentido_positivo"]
        etiqueta_der = "OUT" if etiqueta_izq == "IN" else "IN"

        pos_izq = self._lado_izquierdo(a, b)
        medio = ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)
        pos_der = (2 * medio[0] - pos_izq[0], 2 * medio[1] - pos_izq[1])

        cv2.putText(lienzo, etiqueta_izq, pos_izq, cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        cv2.putText(lienzo, etiqueta_der, pos_der, cv2.FONT_HERSHEY_SIMPLEX, 0.7, GRIS, 2)
        cv2.putText(
            lienzo, linea["nombre"], (a[0], a[1] - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2
        )

    def _dibujar_ayuda(self, lienzo) -> None:
        cv2 = self.cv2
        texto = [
            f"sala: {self.sala_id}   lineas: {len(self.lineas)}",
            f"tipo actual: {self.tipo}   positivo: {self.sentido}",
            "clic A y B para trazar | t tipo | i sentido | z deshacer",
            "espacio avanza cuadro | g guardar | q salir",
        ]
        cv2.rectangle(lienzo, (0, 0), (760, 26 * len(texto) + 12), (30, 30, 30), -1)
        for i, renglon in enumerate(texto):
            cv2.putText(
                lienzo, renglon, (12, 24 + i * 26), cv2.FONT_HERSHEY_SIMPLEX, 0.6, BLANCO, 1
            )

    # --- bucle ------------------------------------------------------------

    def ejecutar(self, salida: Path) -> bool:
        cv2 = self.cv2
        ventana = "Calibracion de lineas"
        cv2.namedWindow(ventana, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(ventana, self._al_hacer_clic)

        while True:
            lienzo = self.cuadro.copy()
            for linea in self.lineas:
                self._dibujar_linea(lienzo, linea)
            if self.punto_a is not None:
                cv2.line(lienzo, self.punto_a, self.cursor, GRIS, 2)
                cv2.circle(lienzo, self.punto_a, 6, GRIS, -1)
            self._dibujar_ayuda(lienzo)

            cv2.imshow(ventana, lienzo)
            tecla = cv2.waitKey(30) & 0xFF

            if tecla == ord("q"):
                cv2.destroyAllWindows()
                return False
            if tecla == ord("g"):
                break
            if tecla == ord("i"):
                self.sentido = "OUT" if self.sentido == "IN" else "IN"
            elif tecla == ord("t"):
                self.tipo = "SALIDA" if self.tipo == "ENTRADA" else "ENTRADA"
            elif tecla == ord("z") and self.lineas:
                eliminada = self.lineas.pop()
                print(f"  eliminada: {eliminada['nombre']}")
            elif tecla == ord(" "):
                leido, cuadro = self.captura.read()
                if leido:
                    self.cuadro = cuadro
            elif tecla == ord("n") and self.lineas:
                nuevo = input("Nombre de la ultima linea: ").strip()
                if nuevo:
                    self.lineas[-1]["nombre"] = nuevo

        cv2.destroyAllWindows()
        self.captura.release()
        return self._guardar(salida)

    def _guardar(self, salida: Path) -> bool:
        entradas = [linea for linea in self.lineas if linea["tipo"] == "ENTRADA"]
        salidas = [linea for linea in self.lineas if linea["tipo"] == "SALIDA"]

        if not entradas or not salidas:
            print("\nADVERTENCIA: hacen falta al menos una linea de ENTRADA y una de")
            print("SALIDA. Sin ambas curvas no hay tiempo de espera que calcular,")
            print("solo un conteo suelto.")

        contenido = {"sala_id": self.sala_id, "lineas": self.lineas}
        salida.write_text(
            yaml.safe_dump(contenido, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        print(f"\nGuardado en {salida}")
        print(f"  {len(entradas)} linea(s) de entrada, {len(salidas)} de salida")
        print("Copiar la seccion `lineas` al archivo config.yaml del agente.")
        return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fuente", required=True, help="URL RTSP, ruta de video o indice de camara local"
    )
    parser.add_argument("--sala", default="LOR-CE-01")
    parser.add_argument("--salida", default="lineas.yaml")
    args = parser.parse_args()

    try:
        calibrador = Calibrador(args.fuente, args.sala)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(__doc__)
    calibrador.ejecutar(Path(args.salida))


if __name__ == "__main__":
    main()

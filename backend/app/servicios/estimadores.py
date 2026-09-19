"""Estimadores del tiempo de espera.

PATRONES APLICADOS
------------------
* Strategy (GoF, comportamiento): la forma de derivar el tiempo de espera a
  partir de los cruces es un algoritmo intercambiable. Antes existia una sola
  funcion `curvas.calcular` cableada en el servicio de consultas; ahora cada
  algoritmo es un objeto con la misma interfaz.
* Template Method (GoF, comportamiento): `EstimadorEspera` fija el esqueleto
  invariante —ordenar los cruces, descartar los resultados implausibles,
  construir el resultado— y delega en las subclases la unica decision que
  distingue a un estimador de otro: como emparejar una salida con su entrada.
* Factory Method (GoF, creacion): `construir_estimador` traduce el nombre
  configurado por sala en la instancia correspondiente.

POR QUE AQUI Y NO EN OTRO SITIO
-------------------------------
La eleccion no es decorativa. El informe de validacion del proyecto compara el
resultado del sistema con observacion manual, y esa comparacion solo tiene
sentido si se puede cambiar de algoritmo sin tocar el resto del sistema. Con la
version anterior, evaluar un estimador alternativo obligaba a editar el modulo
de curvas y volver a desplegar; ahora es un valor en la base de datos.

Ademas, las dos salas del piloto no se comportan igual. El Antonio Lorena esta
saturado y acumula abandonos al cierre; el Regional drena la cola cada dia. Es
razonable que terminen usando estimadores distintos, y el diseno debe admitirlo
sin bifurcaciones dentro del calculo.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.servicios.curvas import ResultadoCurvas, percentil


@dataclass(frozen=True)
class ContextoEstimacion:
    """Todo lo que un estimador necesita saber para trabajar.

    Se agrupa en un objeto en lugar de pasarse como cinco parametros sueltos
    para que anadir un dato nuevo —el aforo directo, por ejemplo— no obligue a
    cambiar la firma de todas las subclases.
    """

    entradas: list[datetime]
    salidas: list[datetime]
    factor_correccion: float = 0.0
    espera_maxima_min: float = 480.0
    metadatos: dict = field(default_factory=dict)


class EstimadorEspera(ABC):
    """Contrato comun de los estimadores. Define el Template Method.

    `estimar` es el metodo plantilla: su cuerpo no se sobrescribe. Las
    subclases implementan unicamente `emparejar`, que es donde reside la
    diferencia real entre un algoritmo y otro.
    """

    #: Nombre con el que se referencia el estimador en la configuracion.
    nombre: str = "base"

    #: Descripcion breve, para el tablero y el informe de validacion.
    descripcion: str = ""

    # --- metodo plantilla: invariante ------------------------------------

    def estimar(self, contexto: ContextoEstimacion) -> ResultadoCurvas:
        entradas = sorted(contexto.entradas)
        salidas = sorted(contexto.salidas)

        resultado = ResultadoCurvas(
            n_entradas=len(entradas),
            n_salidas=len(salidas),
            ocupacion_final=max(0, len(entradas) - len(salidas)),
            ratio_acompanante=contexto.factor_correccion,
        )

        for k, t_salida, t_entrada in self.emparejar(entradas, salidas, contexto):
            espera = (t_salida - t_entrada).total_seconds() / 60.0

            # Una espera negativa significa que las curvas se cruzaron: casi
            # siempre indica geometria de linea mal calibrada o cruces
            # perdidos. Una espera desmedida suele ser un cruce espurio. En
            # ambos casos se descarta el par, pero se cuenta para poder
            # reportar la tasa de descarte.
            if espera < 0 or espera > contexto.espera_maxima_min:
                resultado.descartados += 1
                continue

            resultado.esperas_min.append(espera)
            resultado.pares.append((k, t_entrada, t_salida, espera))

        return resultado

    # --- paso variable: lo unico que cambia entre estimadores -------------

    @abstractmethod
    def emparejar(
        self,
        entradas: list[datetime],
        salidas: list[datetime],
        contexto: ContextoEstimacion,
    ) -> list[tuple[int, datetime, datetime]]:
        """Devuelve las ternas (posicion, instante de salida, instante de entrada)."""


class EstimadorFifoCorregido(EstimadorEspera):
    """Emparejamiento posicional con correccion por entradas sin salida.

    Es el estimador por defecto y el que sustenta la metodologia del proyecto.
    Bajo orden de llegada, quien sale en la posicion k entro en la posicion
    k*(1+r), donde r absorbe toda fuente de entrada sin salida correspondiente:
    acompanantes, abandonos al cierre, personal que cruza la linea.

    Nadie es identificado ni seguido: el emparejamiento es entre posiciones de
    dos curvas acumuladas, no entre personas.
    """

    nombre = "fifo_corregido"
    descripcion = "Curvas de flujo acumulado con factor de corrección por sala"

    def emparejar(self, entradas, salidas, contexto):
        factor = 1.0 + max(0.0, contexto.factor_correccion)
        pares = []
        for k, t_salida in enumerate(salidas):
            indice = round(k * factor)
            if indice >= len(entradas):
                break
            pares.append((k, t_salida, entradas[indice]))
        return pares


class EstimadorFifoEstricto(EstimadorEspera):
    """Emparejamiento posicional sin correccion alguna.

    Se conserva a proposito, aunque se sabe que sobrestima: es la linea de base
    contra la que se mide cuanto aporta la correccion. El informe de validacion
    del proyecto muestra que ignorar las entradas sin salida infla la mediana
    entre un 35 % y un 65 %, y esa cifra solo se puede exhibir si el estimador
    ingenuo sigue siendo ejecutable.
    """

    nombre = "fifo_estricto"
    descripcion = "Emparejamiento uno a uno, sin corrección (línea de base)"

    def emparejar(self, entradas, salidas, contexto):
        return [
            (k, t_salida, entradas[k]) for k, t_salida in enumerate(salidas) if k < len(entradas)
        ]


class EstimadorVentanaMovil(EstimadorEspera):
    """Emparejamiento con el factor recalculado sobre una ventana reciente.

    Responde a una objecion legitima al estimador por defecto: el factor de
    correccion se calibra una vez y se aplica a toda la jornada, pero la
    proporcion de acompanantes no es constante. A primera hora, cuando llegan
    los adultos mayores acompanados, es mas alta que al mediodia.

    Este estimador estima el factor localmente, como el cociente entre
    entradas y salidas observadas en la ventana anterior a cada salida, y lo
    mezcla con el factor calibrado para no reaccionar en exceso a fluctuaciones
    de pocos minutos.
    """

    nombre = "ventana_movil"
    descripcion = "Factor de corrección recalculado sobre una ventana reciente"

    def __init__(self, ventana_min: int = 60, peso_local: float = 0.5):
        self.ventana_min = ventana_min
        # Con peso 0 se comporta como el estimador por defecto; con peso 1
        # ignora la calibracion. El valor intermedio evita ambos extremos.
        self.peso_local = min(1.0, max(0.0, peso_local))

    def emparejar(self, entradas, salidas, contexto):
        base = 1.0 + max(0.0, contexto.factor_correccion)
        ventana = timedelta(minutes=self.ventana_min)
        pares = []

        for k, t_salida in enumerate(salidas):
            desde = t_salida - ventana
            entradas_ventana = sum(1 for t in entradas if desde <= t <= t_salida)
            salidas_ventana = sum(1 for t in salidas if desde <= t <= t_salida)

            if salidas_ventana > 0 and entradas_ventana > 0:
                local = entradas_ventana / salidas_ventana
                factor = base * (1 - self.peso_local) + local * self.peso_local
            else:
                factor = base

            indice = round(k * factor)
            if indice >= len(entradas):
                break
            pares.append((k, t_salida, entradas[indice]))

        return pares


# --- Factory Method --------------------------------------------------------

# Registro abierto a la extension: anadir un estimador nuevo no exige tocar la
# funcion de construccion, solo registrar la clase. Es la diferencia entre un
# factory extensible y una cadena de if encubierta.
_REGISTRO: dict[str, type[EstimadorEspera]] = {}


def registrar_estimador(clase: type[EstimadorEspera]) -> type[EstimadorEspera]:
    """Incorpora un estimador al catalogo. Utilizable como decorador."""
    _REGISTRO[clase.nombre] = clase
    return clase


for _clase in (EstimadorFifoCorregido, EstimadorFifoEstricto, EstimadorVentanaMovil):
    registrar_estimador(_clase)

ESTIMADOR_POR_DEFECTO = EstimadorFifoCorregido.nombre


def construir_estimador(nombre: str | None = None, **parametros) -> EstimadorEspera:
    """Factory Method: traduce el nombre configurado en una instancia."""
    clave = (nombre or ESTIMADOR_POR_DEFECTO).strip().lower()
    clase = _REGISTRO.get(clave)
    if clase is None:
        disponibles = ", ".join(sorted(_REGISTRO))
        raise ValueError(f"Estimador desconocido: {nombre!r}. Disponibles: {disponibles}")
    return clase(**parametros)


def estimadores_disponibles() -> dict[str, str]:
    """Catalogo para el tablero y el informe de validacion."""
    return {nombre: clase.descripcion for nombre, clase in sorted(_REGISTRO.items())}


def comparar_estimadores(contexto: ContextoEstimacion) -> dict[str, dict]:
    """Ejecuta todos los estimadores sobre los mismos datos.

    Es lo que hace posible el capitulo de validacion: la comparacion se produce
    con una sola llamada, sin editar codigo ni volver a desplegar.
    """
    comparacion = {}
    for nombre, clase in sorted(_REGISTRO.items()):
        resultado = clase().estimar(contexto)
        comparacion[nombre] = {
            "descripcion": clase.descripcion,
            "muestras": len(resultado.esperas_min),
            "descartados": resultado.descartados,
            "p50": percentil(resultado.esperas_min, 0.50),
            "p90": percentil(resultado.esperas_min, 0.90),
        }
    return comparacion

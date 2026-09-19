# Sistema de medición del tiempo de espera — Cero Colas

Mide de forma automática y continua el tiempo de espera de los pacientes en la
sala de espera de consultorios externos, mediante visión por computadora, en el
marco del Plan Cero Colas (RM N.° 811-2018/MINSA).

**Establecimientos:** Hospital Antonio Lorena y Hospital Regional del Cusco.

---

## Qué hace y qué no hace

El sistema cuenta **cruces anónimos de personas** en líneas virtuales trazadas
sobre el video, y deriva el tiempo de espera a partir de curvas de flujo
acumulado:

```
L(t) = N_in(t) − N_out(t)          personas esperando en el instante t
W(k) = t_salida(k) − t_entrada(k)  espera de la k-ésima posición de la cola
```

**No** identifica personas, **no** almacena imágenes, **no** realiza
reconocimiento facial ni re-identificación. El video se procesa dentro del
hospital; hacia el servidor solo viajan eventos numéricos.

## Estado

| Componente | Estado |
|---|---|
| API REST (FastAPI) | funcional |
| Modelo de datos y migraciones | funcional |
| Motor de curvas de flujo acumulado | funcional, con pruebas |
| Generador de datos sintéticos | funcional |
| Agente de visión (YOLO + ByteTrack) | funcional, con pruebas |
| Tablero web (React) | funcional |
| Canalización CI/CD (GitHub Actions) | 5 trabajos |
| Despliegue (Neon · Render · Vercel) | configurado |

---

## Puesta en marcha

Requiere Python 3.12. Para desarrollo basta SQLite; PostgreSQL solo es
necesario para el despliegue.

```bash
make instalar          # dependencias
make sembrar           # catálogos, salas, cámaras, líneas y usuarios
make servir            # API en http://localhost:8000/docs
```

En otra terminal, para cargar datos de prueba y ver el sistema funcionando:

```bash
make generar-datos     # 16 600 cruces simulados sobre 24 jornadas
make cargar            # los envía a la API como lo haría el agente real
```

Con Docker:

```bash
cp backend/.env.example .env    # editar JWT_SECRETO y POSTGRES_PASSWORD
docker compose up --build
```

## Pruebas

```bash
make verificar             # lo mismo que ejecuta la canalización
make pruebas-unitarias     # 34 backend + 38 agente
make pruebas-integracion   # 42 backend + 23 agente
make cobertura             # con ramas e informe HTML
```

| Componente | Pruebas | Cobertura de ramas |
|---|---|---|
| Backend | 122 | **100 %** |
| Agente | 80 | 89 % |
| Tablero | `tsc -b` estricto | — |

La medición es **por ramas**, no solo por sentencias: los caminos de error de
este sistema —lote duplicado, línea invertida, sala inexistente, reloj
desajustado, red caída— alcanzarían cobertura completa de sentencias
ejercitando únicamente su rama verdadera.

Los umbrales (90 % backend, 80 % agente) están deliberadamente por debajo de la
cobertura real: un umbral fijado en el valor exacto convierte cualquier
incorporación marginal en un fallo de la construcción, y el equipo termina
desactivando el control.

## Patrones de diseño

Ocho patrones GoF, cada uno resolviendo un problema identificado:

| Patrón | Dónde | Qué resuelve |
|---|---|---|
| Strategy + Template Method | `app/servicios/estimadores.py` | Algoritmo de estimación intercambiable por sala |
| Chain of Responsibility | `app/servicios/validacion.py` | Reglas de validación encadenadas y configurables |
| Observer | `app/servicios/eventos.py` | La ingesta publica; los interesados reaccionan |
| Decorator | `agente/agente/canal.py` | Reintentos y métricas fuera del emisor |
| Factory Method | `construir_estimador` · `construir_detector` | Nombre configurado → implementación |
| Singleton | `obtener_config` con `lru_cache` | Una configuración por proceso |
| Adapter | `DetectorYolo` | Aísla la dependencia de Ultralytics |

Siete patrones más se evaluaron y **se descartaron** con su razón documentada
(ver el documento de ingeniería de software). Un patrón introduce indirección,
y la indirección se paga con dificultad de lectura: solo compensa cuando compra
flexibilidad que el sistema va a usar.

```bash
# El endpoint que hace visible el valor del Strategy:
GET /v1/salas/{codigo}/comparar-estimadores?fecha=YYYY-MM-DD
```

## Canalización CI/CD

`.github/workflows/test_and_build.yaml` — `calidad` → `backend` (matriz
3.11/3.12/3.13) · `agente` · `tablero` → `publicar` en GHCR con prueba de humo
real sobre `/salud` de la imagen recién publicada.

## Base de datos

PostgreSQL 18 con la tabla de cruces particionada por rango de fecha. Consultas
de referencia y diagnóstico en `docs/postgres_consultas.sql`; el documento
*PostgreSQL como motor de persistencia* desarrolla el modelo físico, el
particionado y el dimensionamiento.

Dimensionamiento medido sobre los datos del proyecto: 346 cruces por sala y
jornada, ~284 B por cruce con sus cuatro índices. El plan gratuito de Neon
(0,5 GB) sostiene **8,5 años** del piloto de dos salas y **10 meses** a escala
regional de veinte salas.

## Despliegue

Neon (PostgreSQL) → Render (API) → Vercel (tablero), en ese orden: cada uno
produce un valor que el siguiente necesita. Ver la **Guía de implementación**.

---

## Verificación de extremo a extremo

Cargando los datos sintéticos y consultando `/v1/indicadores`, el sistema
recupera las medianas de la línea base de KoboCollect:

| Sala | Sin corrección | Corregido | Línea base KoboCollect |
|---|---|---|---|
| LOR-CE-01 | 205,8 min | **159,5 min** | 150 min |
| REG-CE-01 | 91,0 min | **53,4 min** | 55 min |

Contra la verdad de terreno sintética, tras ajustar el factor de corrección:

| Sala | Sesgo del P50 | Error absoluto medio |
|---|---|---|
| LOR-CE-01 | −0,2 min | 8,3 min |
| REG-CE-01 | +0,0 min | 4,4 min |

```bash
make validar        # compara la API contra la verdad de terreno
make ajustar-ratio  # ajusta el factor de corrección de cada sala
```

### El factor de corrección no es un conteo de acompañantes

Ignorar a los acompañantes sobrestima la espera entre 35 % y 65 %: cada uno
genera un cruce de entrada sin su correspondiente cruce de salida, lo que
desplaza la curva `N_in`. El parámetro `ratio_acompanante` de cada sala corrige
el emparejamiento posicional.

Al validar apareció algo que conviene tener presente. Los datos sintéticos se
generaron con **la misma proporción real de acompañantes (0,34) en ambas
salas**, pero el factor que anula el sesgo resultó distinto: **0,271 en el
Lorena y 0,316 en el Regional**. Con 0,34 en ambas, el Lorena quedaba con un
sesgo sistemático de −10,7 minutos.

La razón es que el parámetro absorbe toda fuente de entradas sin salida
correspondiente, no solo acompañantes:

- pacientes que abandonan o quedan sin atención al cierre de la jornada,
- personal que cruza la línea de entrada,
- atenciones por prioridad, que rompen el emparejamiento posicional y pesan
  más cuanto más larga es la cola.

El Lorena está más saturado, así que acumula más de estos efectos. La
consecuencia práctica para el trabajo de campo: **el factor debe ajustarse por
sala contra observación manual, no obtenerse contando acompañantes en la
puerta**. Un sesgo sistemático en el informe de validación indicará un factor
mal calibrado; el error absoluto residual refleja la variabilidad natural entre
jornadas, que ningún parámetro elimina.

---

## Estructura

```
backend/
  app/
    main.py            aplicación FastAPI
    config.py          configuración por variables de entorno
    models.py          modelos ORM (diagrama entidad-relación)
    schemas.py         contratos de entrada y salida
    security.py        JWT, hash de contraseñas, roles
    routers/           auth, ingesta, indicadores, calibración
    servicios/
      curvas.py        motor de curvas de flujo acumulado
      consultas.py     acceso a datos
      tiempo.py        zona horaria y fecha operativa
  alembic/             migraciones, incluido el particionado
  scripts/
    sembrar.py         catálogos y usuarios iniciales
    cargar_sinteticos.py  simula el agente contra la API
  tests/
datos_sinteticos/      generador y CSV producidos
agente/                agente de visión (ver agente/README.md)
  agente/
    principal.py       bucle principal
    conteo.py          geometría de cruce de líneas
    deteccion.py       YOLO11n + ByteTrack, y detector simulado
    captura.py         RTSP con reconexión automática
    cola.py            cola local SQLite
    emisor.py          envío idempotente con reintentos
  herramientas/
    calibrar_lineas.py trazado visual de las líneas
  tests/
tablero/               interfaz React (ver tablero/README.md)
```

### Tablero

```bash
cd tablero && npm install && npm run dev    # http://localhost:5173
```

El centro de la pantalla es la curva de flujo acumulado con un **calibrador**
dibujado encima: una llave entre ambas curvas rotulada con la espera que
representa, anclada en la mediana de la jornada. Enseña el método mientras
muestra el dato, que es lo que necesita tanto la jefatura como quien evalúa la
validez de la medición.

### Agente de visión

Corre en el equipo de borde de cada hospital. El video se procesa en memoria y
se descarta: no se guarda ningún cuadro ni rasgo biométrico. Solo salen del
hospital instantes de cruce anónimos.

```bash
cd agente && pip install -r requirements-dev.txt
cp config.ejemplo.yaml config.yaml
export AGENTE_EMAIL=agente@cerocolas.local AGENTE_PASSWORD=...
python -m agente.principal --config config.yaml
```

Con `fuente: simulada` y `detector: simulado` la cadena completa funciona sin
cámara ni modelo, lo que permite desarrollar el tablero antes de instalar el
hardware. Verificado: con la API caída el agente contó y encoló 390 cruces, y
al restablecerse el servicio los envió todos.

## Endpoints

| Método | Ruta | Rol |
|---|---|---|
| POST | `/v1/auth/token` | público |
| POST | `/v1/cruces:lote` | AGENTE |
| POST | `/v1/aforos` | AGENTE |
| GET | `/v1/salas/{codigo}/estado` | VISOR |
| GET | `/v1/salas/{codigo}/curvas` | VISOR |
| GET | `/v1/indicadores` | VISOR |
| GET | `/v1/catalogos/salas` | VISOR |
| POST | `/v1/calibraciones` | GESTOR |
| GET | `/v1/calibraciones/resumen` | VISOR |
| GET | `/salud` | público |

Documentación interactiva en `/docs`; especificación OpenAPI en
`/openapi.json`.

### Idempotencia

Todo `POST /v1/cruces:lote` exige la cabecera `Idempotency-Key`. El agente
trabaja con red inestable y reintenta; sin esta garantía un reintento
duplicaría cruces y falsearía las curvas. Un segundo envío con la misma clave
devuelve el resultado del primero sin insertar nada.

### Separación de permisos

El rol `AGENTE` solo escribe eventos y no puede leer indicadores. Si el equipo
de borde de un hospital fuera comprometido, no expondría información.

---

## Decisiones de diseño

**Los tiempos de espera no se almacenan como dato editable.** Se derivan
siempre de los cruces. Esto hace la información auditable y permite recalcular
toda la historia si se corrige la geometría de una línea.

**Los establecimientos se identifican por código RENIPRESS**, no por nombre: en
la base manual el mismo hospital aparecía escrito de varias formas.

**Toda marca temporal exige desfase horario explícito** y se almacena en UTC.
La base manual no tenía zona horaria y eso ya había generado ambigüedades. Se
guardan por separado el instante reportado por el agente y el de recepción en
el servidor, lo que permite detectar desfases de reloj en el equipo de borde.

**Se reportan P50 y P90, no promedios.** La distribución de esperas tiene cola
derecha larga: en la base manual la media era el doble de la mediana por efecto
de unos pocos casos extremos.

**La fecha operativa corta a las 3 a.m.**, no a medianoche, porque la cola de
estos hospitales empieza de madrugada.

---

## Advertencias

Los datos de `datos_sinteticos/` son **ficticios**. No deben presentarse como
mediciones ni mezclarse con la base de KoboCollect.

Las credenciales creadas por `make sembrar` son de desarrollo y deben cambiarse
antes de cualquier despliegue.

**Licenciamiento:** el agente de visión usará YOLO, cuya implementación de
referencia se distribuye bajo AGPL-3.0. Para un piloto interno no representa
obstáculo, pero la adopción institucional obligaría a liberar el código o a
adquirir licencia comercial. Alternativas con licencia permisiva: RT-DETR o
YOLOX (Apache 2.0). Esta decisión debe tomarse antes de escribir el agente.

**Marco legal:** captar imágenes en un establecimiento de salud constituye
tratamiento de datos personales bajo la Ley N.° 29733. El despliegue requiere
autorización escrita de la dirección de cada hospital y señalética visible.

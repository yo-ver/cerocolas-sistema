# Aviso de licencias

Este proyecto se distribuye bajo **AGPL-3.0-or-later** (ver `LICENSE`).

## Por qué AGPL y no una licencia permisiva

El agente de visión utiliza la implementación de referencia de YOLO
(Ultralytics), distribuida bajo AGPL-3.0. Esa licencia es *copyleft fuerte*: se
propaga a cualquier obra derivada y sus obligaciones se activan tanto al
distribuir el software como al ofrecerlo como servicio en red.

Conviene precisar un punto que suele confundirse: **el uso académico no exime
de la AGPL**. Lo que activa las obligaciones no es el propósito sino la
distribución. Mantener el proyecto en las máquinas del equipo no genera
ninguna; publicarlo en un repositorio abierto sí.

Como este proyecto se publica en GitHub para su evaluación académica, la vía de
cumplimiento más simple —y la adoptada— es licenciar el repositorio completo
bajo la misma AGPL-3.0. Con ello el proyecto queda en regla sin necesidad de
adquirir licencia comercial ni de sustituir el detector.

## Si el sistema se adoptara institucionalmente

Si en el futuro un establecimiento de salud quisiera operar este sistema sin
publicar su código, existen dos caminos:

1. Adquirir una licencia comercial de Ultralytics.
2. Sustituir el detector por uno con licencia permisiva —**RT-DETR** o
   **YOLOX**, ambos Apache 2.0— reimplementando únicamente la clase
   `DetectorYolo` de `agente/agente/deteccion.py`. El resto del sistema no
   cambia: la interfaz `Detector` aísla esa decisión.

## Componentes de terceros

| Componente | Licencia | Uso |
|---|---|---|
| Ultralytics YOLO | AGPL-3.0 | Detección de personas en el agente |
| ByteTrack | MIT | Seguimiento entre cuadros |
| FastAPI, Starlette, Pydantic | MIT | API |
| SQLAlchemy, Alembic | MIT | Acceso a datos y migraciones |
| React, Vite, Recharts | MIT | Tablero |
| OpenCV | Apache 2.0 | Captura de video |
| PostgreSQL | PostgreSQL License | Base de datos |

## Datos

Los archivos de `datos_sinteticos/` son **simulados**. No provienen de ningún
establecimiento de salud, no deben presentarse como mediciones ni mezclarse con
la base de KoboCollect del Plan Cero Colas.

El sistema no almacena imágenes ni datos personales: los agentes de visión
procesan el video en memoria y transmiten únicamente eventos numéricos
anónimos.

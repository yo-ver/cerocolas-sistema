# Agente de visión

Detecta cruces anónimos de personas en líneas virtuales y los envía a la API.
Corre en el equipo de borde instalado en cada hospital, junto a las cámaras.

## Privacidad

El video se procesa en memoria y se descarta. **No se guarda ningún cuadro, ni
rostro, ni rasgo biométrico.** Lo único que persiste y sale del hospital es una
lista de instantes en que alguien anónimo cruzó una línea:

```json
{"linea": "ENTRADA_SALA", "direccion": "IN",
 "ocurrido_en": "2026-08-15T07:12:04-05:00", "confianza": 0.91}
```

El identificador de seguimiento se reinicia con cada arranque, solo vive los
segundos que dura el cruce y no permite reconstruir a ninguna persona.

## Puesta en marcha

```bash
pip install -r requirements-dev.txt
cp config.ejemplo.yaml config.yaml

export AGENTE_EMAIL=agente@cerocolas.local
export AGENTE_PASSWORD=...
export AGENTE_RTSP="rtsp://usuario:clave@192.168.1.10:554/stream1"

python -m agente.principal --config config.yaml
```

Sin cámara ni modelo, para probar toda la cadena:

```yaml
video:    {fuente: simulada}
detector: {tipo: simulado}
```

## Calibración de las líneas

Se hace durante la visita técnica, con el video ya encuadrado:

```bash
python herramientas/calibrar_lineas.py --fuente "$AGENTE_RTSP" --sala LOR-CE-01
```

Clic en A y en B para trazar cada línea; `t` alterna entrada/salida, `i`
alterna la dirección, `g` guarda. Luego se copia la sección `lineas` al
`config.yaml`.

**Convención de dirección:** quien cruza hacia el lado **izquierdo de la flecha
A→B tal como se ve en pantalla** genera `sentido_positivo`. La herramienta
dibuja la flecha y rotula ambos lados, porque en coordenadas de imagen el eje
vertical crece hacia abajo y la intuición algebraica se invierte. Es el error
de calibración más común: durante el desarrollo de este agente ocurrió, y el
backend lo detectó rechazando los cruces con el motivo *"la línea X es de tipo
ENTRADA y solo admite dirección IN"*. Si aparece ese mensaje, la flecha está al
revés.

## Arquitectura

```
captura (hilo)  →  detección  →  conteo  →  cola SQLite
                                                 ↓
                                          emisor (hilo)  →  API
```

**El envío va en su propio hilo** para que un problema de red no detenga la
medición. Si el servidor no responde, la captura continúa y los cruces se
acumulan; cuando la red vuelve, la cola se vacía sola. La medición perdida no
se recupera; el envío atrasado sí.

**La cola es SQLite en archivo**, no en memoria, porque el equipo también se
apaga: un corte de energía no debe llevarse la jornada.

**Idempotencia:** la clave se deriva del contenido del lote. Si el agente se
reinicia tras enviar pero antes de confirmar, el reenvío produce la misma clave
y el servidor lo reconoce como duplicado.

Verificado en la práctica: con la API caída, el agente contó y encoló 390
cruces; al restablecerse el servicio los envió todos y quedó en cero pendientes.

## Decisiones de diseño

**El punto de anclaje es la base del recuadro, no su centro.** Una persona se
detecta como un rectángulo alto; con el centro, alguien alto y alguien bajo
cruzarían la línea en momentos distintos aunque pisen el mismo punto del piso.
La base aproxima dónde están los pies.

**La línea es un segmento, no una recta infinita.** Sin verificar que la
proyección cae dentro del tramo, se contaría a quien camina al otro extremo de
la sala solo por estar del otro lado de la prolongación.

**Hay una espera mínima de 2 segundos entre cruces del mismo objeto.** Alguien
detenido justo sobre la línea oscila un píxel entre cuadros y generaría decenas
de cruces alternados.

**Los identificadores se olvidan al perderse el seguimiento.** Los seguidores
reciclan identificadores; si un objeto nuevo heredara el lado previo de uno
viejo, se contaría un cruce que nunca ocurrió.

**10 fps son suficientes.** Contar cruces no exige video completo, y bajar la
tasa permite correr dos flujos en un equipo compacto sin GPU.

## Pruebas

```bash
python -m pytest tests/ -v      # 17 pruebas
```

La lógica de cruce se prueba sin cámara ni modelo, que es la razón de haberla
separado del detector. Cubren: anclaje en la base, cruce en ambos sentidos,
rechazo fuera del tramo, rebote sobre la línea, reciclado de identificadores,
varias personas simultáneas, y persistencia de la cola tras reinicio.

## Hardware

Por establecimiento: dos cámaras IP con RTSP (lente 2,8 mm, a 2,5–3 m de altura
con ángulo picado de 30°–45°), un equipo compacto tipo Intel N100, conmutador
con PoE y punto de energía permanente.

## Licencia

El detector usa Ultralytics YOLO, bajo **AGPL-3.0**. Este es un proyecto
académico sin despliegue en establecimientos de salud, de modo que la licencia
no representa obstáculo; si el repositorio se publica, debe licenciarse también
como AGPL-3.0. Alternativas permisivas si eso cambiara: RT-DETR o YOLOX
(Apache 2.0), intercambiables reimplementando solo `DetectorYolo`.

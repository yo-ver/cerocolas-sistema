# Tablero

Interfaz de consulta del sistema de medición del tiempo de espera. Responde una
sola pregunta: **cuánto se está esperando ahora, y si hoy es peor que lo
habitual.**

```bash
npm install
npm run dev        # http://localhost:5173
```

Requiere el backend activo en `http://127.0.0.1:8000` (el servidor de
desarrollo hace de proxy hacia `/v1`). Para apuntar a otro servidor:
`VITE_API_URL=https://…  npm run build`.

Credenciales de desarrollo: `visor@cerocolas.local` / `cambiar-visor`.

## Decisiones de diseño

**El héroe es la curva, no una fila de números.** En este sistema la separación
entre dos líneas *es* la medición: la distancia vertical son las personas que
esperan, la horizontal el tiempo que esperan. Un gráfico de líneas limpio
mostraría el dato y ocultaría el método. Por eso se dibuja encima un
**calibrador**: una llave entre ambas curvas, rotulada con la espera que
representa. El público incluye a quien debe convencerse de que la medición es
válida y a una jefatura que nunca ha visto una curva de flujo acumulado.

**El calibrador se ancla en la espera mediana**, no en la última salida ni en
la mitad de la fila. Ambas alternativas son engañosas: cuando la cola ya se
drenó, el último en pasar casi no esperó; y quien está a mitad de fila suele
haber llegado en el pico y espera más que la mediana. Anclarlo en la mediana
hace que la llave dibujada y la cifra del titular coincidan. Al mover el
puntero, la llave recorre la jornada.

**El vocabulario de estados es el del formulario MINSA.** `ADECUADO`, `LARGO`,
`MUY LARGO` es la escala con que el personal ya califica la percepción del
usuario en el Plan Cero Colas. Reutilizarla evita imponer un vocabulario
paralelo.

**Los umbrales sí son una decisión del proyecto**, y la interfaz lo declara al
pie. No hay cifra oficial para sala de espera de consultorios externos. Se
anclan en 60 y 120 minutos, a partir de la distribución de la línea base
(mediana de 55 min en el Regional, 150 min en el Lorena). Con un umbral de
ventanilla —del orden de 30 min— las 30 jornadas de ambos hospitales salían
`MUY LARGO`, y un indicador que nunca cambia no informa nada. Fijarlos en firme
corresponde a la institución.

**Toda cifra en monoespaciada.** No es un gesto estético: al comparar P50 y P90
entre jornadas, las columnas deben alinearse dígito con dígito. IBM Plex Sans
para texto, Plex Mono para datos y códigos, Plex Sans Condensed para títulos.

## Estructura

```
src/
  api.ts                    cliente tipado; la forma del contrato vive aquí
  espera.ts                 clasificación, umbrales y formato de duraciones
  App.tsx                   ingreso, selección de sala, refresco periódico
  componentes/
    CurvasFlujo.tsx         gráfico con el calibrador
    Paneles.tsx             cifras, historial de 30 días, tabla de jornadas
  estilos.css               tokens y estilos
```

Se refresca cada 30 segundos. El token vive en `sessionStorage`: se cierra la
pestaña, se vuelve a ingresar.

## Detalle de implementación que conviene conocer

Recharts **no recorre Fragments** al identificar sus hijos. Las `ReferenceLine`
del calibrador envueltas en `<>…</>` se montaban sin dibujarse, sin error ni
advertencia. Se pasan como arreglo con `key`. Si en el futuro se agregan
anotaciones al gráfico, deben seguir la misma forma.

## Accesibilidad

Foco visible en todos los controles, barras del historial navegables con
teclado y con `aria-label` descriptivo, `prefers-reduced-motion` respetado,
diseño utilizable hasta 640 px. Las curvas se distinguen por matiz y no por
intensidad, de modo que siguen siendo legibles proyectadas y en impresión en
escala de grises.

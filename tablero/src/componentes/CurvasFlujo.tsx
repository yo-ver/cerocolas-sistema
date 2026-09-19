/* Curvas de flujo acumulado.
 *
 * Es el elemento central del tablero, y la razon es metodologica: en este
 * sistema la separacion entre dos lineas ES la medicion. La distancia vertical
 * son las personas que esperan; la horizontal, el tiempo que esperan. Un
 * grafico de lineas limpio mostraria el dato pero ocultaria el metodo.
 *
 * Por eso se dibuja encima un calibrador: una llave horizontal entre ambas
 * curvas, rotulada con la espera que representa. El publico de esta pantalla
 * incluye a quien debe convencerse de que la medicion es valida y a una
 * jefatura que nunca ha visto una curva de flujo acumulado. El calibrador
 * ensena el metodo mientras muestra la jornada.
 */

import { useMemo, useState } from "react";
import {
  Area,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { PuntoCurva } from "../api";
import { formatearDuracion, formatearHora } from "../espera";

interface Props {
  puntos: PuntoCurva[];
  ratioAcompanante: number;
}

interface Fila {
  ms: number;
  hora: string;
  n_in: number;
  n_out: number;
  ocupacion: number;
  /* Banda entre ambas curvas: [n_out, n_in]. Recharts la dibuja como area de
     rango, lo que hace visible la ocupacion sin una serie adicional. */
  banda: [number, number];
}

/** Instante en que la curva de entradas alcanzo por primera vez el nivel dado. */
function cruceDeNivel(filas: Fila[], nivel: number): number | null {
  for (const fila of filas) {
    if (fila.n_in >= nivel) return fila.ms;
  }
  return null;
}

export function CurvasFlujo({ puntos, ratioAcompanante }: Props) {
  const [seleccion, setSeleccion] = useState<number | null>(null);

  const filas = useMemo<Fila[]>(
    () =>
      puntos.map((punto) => ({
        ms: new Date(punto.t).getTime(),
        hora: formatearHora(punto.t),
        n_in: punto.n_in,
        n_out: punto.n_out,
        ocupacion: punto.ocupacion,
        banda: [punto.n_out, punto.n_in],
      })),
    [puntos],
  );

  /* El calibrador se ancla por defecto en la persona cuya espera es la MEDIANA
     de la jornada, de modo que lo que se ve dibujado sobre el grafico sea la
     misma cifra que encabeza la pantalla.
     
     Dos anclas mas simples se descartaron por enganosas: la ultima salida
     muestra una espera corta, porque cuando la cola ya se drenó el ultimo en
     pasar casi no espero; y la posicion media de la fila tampoco sirve, porque
     quien esta a mitad de cola suele haber llegado en el pico de congestion y
     espera mas que la mediana. Al pasar el puntero la llave se mueve, de modo
     que se puede recorrer la jornada. */
  const calibrador = useMemo(() => {
    if (filas.length < 2) return null;

    const factor = 1 + ratioAcompanante;

    const medir = (fila: Fila) => {
      if (fila.n_out === 0) return null;
      const nivelEntrada = Math.round(fila.n_out * factor);
      const msEntrada = cruceDeNivel(filas, nivelEntrada);
      if (msEntrada === null || msEntrada >= fila.ms) return null;
      return {
        nivel: fila.n_out,
        desde: msEntrada,
        hasta: fila.ms,
        minutos: (fila.ms - msEntrada) / 60000,
        ocupacion: fila.ocupacion,
      };
    };

    if (seleccion !== null) {
      const fila = filas.find((item) => item.ms === seleccion);
      return fila ? medir(fila) : null;
    }

    const medidas = filas.map(medir).filter((item) => item !== null);
    if (medidas.length === 0) return null;

    const ordenadas = [...medidas].sort((a, b) => a.minutos - b.minutos);
    const mediana = ordenadas[Math.floor(ordenadas.length / 2)].minutos;

    return medidas.reduce((mejor, item) =>
      Math.abs(item.minutos - mediana) < Math.abs(mejor.minutos - mediana)
        ? item
        : mejor,
    );
  }, [filas, seleccion, ratioAcompanante]);

  if (filas.length < 2) {
    return (
      <div className="aviso">
        <p className="aviso__titulo">Todavía no hay movimiento hoy</p>
        <p className="aviso__texto">
          Las curvas aparecen cuando el agente registra los primeros cruces de la
          jornada. Si la sala ya está abierta, revisa que el agente esté
          conectado.
        </p>
      </div>
    );
  }

  const maximo = Math.max(...filas.map((fila) => fila.n_in));

  return (
    <div className="grafico">
      <ResponsiveContainer width="100%" height={330}>
        <ComposedChart
          data={filas}
          margin={{ top: 28, right: 24, bottom: 8, left: 4 }}
          onMouseMove={(estado) => {
            const fila = estado?.activePayload?.[0]?.payload as Fila | undefined;
            setSeleccion(fila ? fila.ms : null);
          }}
          onMouseLeave={() => setSeleccion(null)}
        >
          <XAxis
            dataKey="ms"
            type="number"
            scale="time"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(ms: number) =>
              new Date(ms).toLocaleTimeString("es-PE", {
                hour: "2-digit",
                minute: "2-digit",
                hour12: false,
              })
            }
            stroke="#6b7c88"
            tick={{ fontSize: 11, fontFamily: "IBM Plex Mono" }}
            tickLine={false}
          />
          <YAxis
            stroke="#6b7c88"
            tick={{ fontSize: 11, fontFamily: "IBM Plex Mono" }}
            tickLine={false}
            axisLine={false}
            width={40}
            domain={[0, Math.ceil((maximo * 1.08) / 25) * 25]}
          />

          <Area
            dataKey="banda"
            fill="#4b3fa8"
            fillOpacity={0.07}
            stroke="none"
            isAnimationActive={false}
          />

          <Line
            dataKey="n_in"
            stroke="#0e7c66"
            strokeWidth={2.5}
            dot={false}
            isAnimationActive={false}
            name="Llegadas acumuladas"
          />
          <Line
            dataKey="n_out"
            stroke="#4b3fa8"
            strokeWidth={2.5}
            dot={false}
            isAnimationActive={false}
            name="Pasos a consultorio"
          />

          {calibrador && [
              <ReferenceLine
                key="calibrador-tramo"
                segment={[
                  { x: calibrador.desde, y: calibrador.nivel },
                  { x: calibrador.hasta, y: calibrador.nivel },
                ]}
                stroke="#a4161a"
                strokeWidth={1.5}
                strokeDasharray="5 3"
                label={{
                  value: `${seleccion === null ? "espera típica" : "espera"} ${formatearDuracion(calibrador.minutos)}`,
                  position: "top",
                  fill: "#a4161a",
                  fontSize: 12,
                  fontFamily: "IBM Plex Mono",
                  fontWeight: 600,
                }}
                ifOverflow="extendDomain"
              />,
              <ReferenceLine
                key="calibrador-tope-izq"
                segment={[
                  { x: calibrador.desde, y: Math.max(0, calibrador.nivel - maximo * 0.03) },
                  { x: calibrador.desde, y: calibrador.nivel + maximo * 0.03 },
                ]}
                stroke="#a4161a"
                strokeWidth={2}
              />,
              <ReferenceLine
                key="calibrador-tope-der"
                segment={[
                  { x: calibrador.hasta, y: Math.max(0, calibrador.nivel - maximo * 0.03) },
                  { x: calibrador.hasta, y: calibrador.nivel + maximo * 0.03 },
                ]}
                stroke="#a4161a"
                strokeWidth={2}
              />,
          ]}

          <Tooltip
            cursor={{ stroke: "#6b7c88", strokeDasharray: "3 3" }}
            contentStyle={{
              border: "1px solid #cfd8de",
              borderRadius: 4,
              fontSize: 13,
              fontFamily: "IBM Plex Mono",
              boxShadow: "0 4px 16px rgba(22,35,46,0.1)",
            }}
            labelFormatter={(ms) =>
              new Date(ms as number).toLocaleTimeString("es-PE", {
                hour: "2-digit",
                minute: "2-digit",
                hour12: false,
              })
            }
            formatter={(valor, nombre) => {
              if (nombre === "banda") return [null, null];
              const etiquetas: Record<string, string> = {
                n_in: "Llegadas",
                n_out: "A consultorio",
                ocupacion: "En sala",
              };
              return [valor as number, etiquetas[nombre as string] ?? nombre];
            }}
          />
        </ComposedChart>
      </ResponsiveContainer>

      {calibrador && (
        <p
          style={{
            margin: "4px 12px 0",
            fontSize: 12.5,
            color: "#6b7c88",
            fontFamily: "IBM Plex Sans",
          }}
        >
          La llave roja mide la persona número{" "}
          <strong style={{ fontFamily: "IBM Plex Mono", color: "#16232e" }}>
            {calibrador.nivel}
          </strong>{" "}
          en pasar a consultorio: entró y salió con{" "}
          <strong style={{ fontFamily: "IBM Plex Mono", color: "#a4161a" }}>
            {formatearDuracion(calibrador.minutos)}
          </strong>{" "}
          de diferencia. El grosor vertical de la banda son las{" "}
          <strong style={{ fontFamily: "IBM Plex Mono", color: "#16232e" }}>
            {calibrador.ocupacion}
          </strong>{" "}
          personas que esperaban en ese momento. Mueve el puntero sobre el
          gráfico para recorrer la jornada.
        </p>
      )}
    </div>
  );
}

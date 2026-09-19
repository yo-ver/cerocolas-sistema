/* Cifras del dia e historial reciente. */

import { useState } from "react";

import type { EstadoSala, IndicadorDia } from "../api";
import {
  COLORES,
  ETIQUETAS,
  clasificar,
  formatearDuracion,
  formatearFecha,
  formatearFechaLarga,
} from "../espera";

function Distintivo({ minutos }: { minutos: number | null }) {
  const nivel = clasificar(minutos);
  if (!nivel) return null;
  return (
    <span className={`estado estado--${nivel}`}>
      <span className="estado__punto" aria-hidden="true" />
      {ETIQUETAS[nivel]}
    </span>
  );
}

export function Cifras({ estado }: { estado: EstadoSala }) {
  const nivel = clasificar(estado.espera_p50_min);

  return (
    <div className="cifras">
      <div className="cifra">
        <p className="cifra__etiqueta">Esperando ahora</p>
        <div className="cifra__valor">
          {estado.ocupacion}
          <span className="cifra__unidad">personas</span>
        </div>
        <p className="cifra__pie">
          {estado.n_entradas} llegadas · {estado.n_salidas} pasaron a consultorio
        </p>
      </div>

      <div
        className="cifra"
        style={nivel ? { borderTopColor: COLORES[nivel] } : undefined}
      >
        <p className="cifra__etiqueta">Espera típica de hoy · P50</p>
        <div
          className="cifra__valor"
          style={nivel ? { color: COLORES[nivel] } : undefined}
        >
          {formatearDuracion(estado.espera_p50_min)}
        </div>
        <p className="cifra__pie">
          <Distintivo minutos={estado.espera_p50_min} />
        </p>
      </div>

      <div className="cifra">
        <p className="cifra__etiqueta">Peor décimo · P90</p>
        <div className="cifra__valor">{formatearDuracion(estado.espera_p90_min)}</div>
        <p className="cifra__pie">
          1 de cada 10 personas esperó al menos esto
        </p>
      </div>

      <div className="cifra">
        <p className="cifra__etiqueta">Si llegara ahora</p>
        <div className="cifra__valor">
          {formatearDuracion(estado.espera_estimada_min)}
        </div>
        <p className="cifra__pie">
          {estado.espera_estimada_min === null
            ? "Sin atenciones en la última hora, no hay ritmo que proyectar"
            : "Proyección según el ritmo de atención de la última hora"}
        </p>
      </div>
    </div>
  );
}

export function Historial({ filas }: { filas: IndicadorDia[] }) {
  const [activa, setActiva] = useState<IndicadorDia | null>(null);

  if (filas.length === 0) {
    return (
      <div className="aviso">
        <p className="aviso__titulo">Sin jornadas anteriores</p>
        <p className="aviso__texto">
          El historial se construye solo, jornada a jornada, a medida que el
          agente reporta.
        </p>
      </div>
    );
  }

  const maximo = Math.max(...filas.map((fila) => fila.espera_p90_min ?? 0), 1);
  const mostrada = activa ?? filas[filas.length - 1];

  return (
    <div className="historial">
      <div className="historial__barras">
        {filas.map((fila) => {
          const valor = fila.espera_p50_min ?? 0;
          const nivel = clasificar(fila.espera_p50_min);
          return (
            <button
              key={fila.fecha_operativa}
              className="historial__barra"
              style={{
                height: `${Math.max((valor / maximo) * 100, 2)}%`,
                background: nivel ? COLORES[nivel] : "#cfd8de",
                opacity: activa && activa !== fila ? 0.45 : 1,
              }}
              onMouseEnter={() => setActiva(fila)}
              onFocus={() => setActiva(fila)}
              onMouseLeave={() => setActiva(null)}
              onBlur={() => setActiva(null)}
              aria-label={`${formatearFechaLarga(fila.fecha_operativa)}: espera típica ${formatearDuracion(fila.espera_p50_min)}`}
            />
          );
        })}
      </div>

      <div className="historial__eje">
        <span>{formatearFecha(filas[0].fecha_operativa)}</span>
        <span>{formatearFecha(filas[filas.length - 1].fecha_operativa)}</span>
      </div>

      <p className="historial__detalle">
        {formatearFechaLarga(mostrada.fecha_operativa)} · espera típica{" "}
        <strong>{formatearDuracion(mostrada.espera_p50_min)}</strong> · peor
        décimo <strong>{formatearDuracion(mostrada.espera_p90_min)}</strong> ·{" "}
        <strong>{mostrada.n_pacientes}</strong> personas medidas
        {mostrada.descartados > 0 && (
          <> · {mostrada.descartados} mediciones descartadas por implausibles</>
        )}
      </p>
    </div>
  );
}

export function TablaJornadas({ filas }: { filas: IndicadorDia[] }) {
  const recientes = [...filas].reverse().slice(0, 10);

  return (
    <table className="tabla">
      <thead>
        <tr>
          <th scope="col">Jornada</th>
          <th scope="col" style={{ textAlign: "right" }}>
            Personas
          </th>
          <th scope="col" style={{ textAlign: "right" }}>
            P50
          </th>
          <th scope="col" style={{ textAlign: "right" }}>
            P90
          </th>
          <th scope="col" style={{ textAlign: "right" }}>
            Máximo en sala
          </th>
          <th scope="col">Estado</th>
        </tr>
      </thead>
      <tbody>
        {recientes.map((fila) => (
          <tr key={fila.fecha_operativa}>
            <td>{formatearFechaLarga(fila.fecha_operativa)}</td>
            <td className="num">{fila.n_pacientes}</td>
            <td className="num">{formatearDuracion(fila.espera_p50_min)}</td>
            <td className="num">{formatearDuracion(fila.espera_p90_min)}</td>
            <td className="num">{fila.ocupacion_maxima}</td>
            <td>
              <Distintivo minutos={fila.espera_p50_min} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

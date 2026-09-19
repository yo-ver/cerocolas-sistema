/* Tablero de tiempo de espera en sala de espera de consultorios externos.
 *
 * Una sola pregunta organiza la pantalla: cuanto se esta esperando ahora, y si
 * hoy es peor que lo habitual. Todo lo demas es subordinado a eso.
 */

import { useCallback, useEffect, useState } from "react";

import {
  type EstadoSala,
  type IndicadorDia,
  type PuntoCurva,
  type Sala,
  ErrorApi,
  borrarToken,
  ingresar,
  leerToken,
  obtenerCurvas,
  obtenerEstado,
  obtenerIndicadores,
  obtenerSalas,
} from "./api";
import { CurvasFlujo } from "./componentes/CurvasFlujo";
import { Cifras, Historial, TablaJornadas } from "./componentes/Paneles";
import { UMBRALES, formatearFechaLarga, hoyOperativo, restarDias } from "./espera";

const INTERVALO_REFRESCO_MS = 30_000;
const DIAS_HISTORIAL = 30;

/* --- ingreso -------------------------------------------------------------- */

function Ingreso({ alEntrar }: { alEntrar: () => void }) {
  const [email, setEmail] = useState("visor@cerocolas.local");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  async function enviar(evento: React.FormEvent) {
    evento.preventDefault();
    setEnviando(true);
    setError(null);
    try {
      await ingresar(email, password);
      alEntrar();
    } catch (excepcion) {
      setError(
        excepcion instanceof ErrorApi
          ? excepcion.message
          : "No se pudo conectar con la API.",
      );
    } finally {
      setEnviando(false);
    }
  }

  return (
    <div className="ingreso">
      <form className="tarjeta ingreso__caja" onSubmit={enviar}>
        <h1 className="ingreso__titulo">Tiempo de espera</h1>
        <p className="ingreso__nota">
          Consultorios externos · Plan Cero Colas
        </p>

        {error && <p className="error-texto">{error}</p>}

        <label className="campo">
          <span className="campo__etiqueta">Correo</span>
          <input
            className="campo__entrada"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="username"
            required
          />
        </label>

        <label className="campo">
          <span className="campo__etiqueta">Clave</span>
          <input
            className="campo__entrada"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>

        <button className="boton" type="submit" disabled={enviando}>
          {enviando ? "Entrando…" : "Entrar"}
        </button>
      </form>
    </div>
  );
}

/* --- aplicacion ----------------------------------------------------------- */

export default function App() {
  const [autenticado, setAutenticado] = useState(() => leerToken() !== null);
  const [salas, setSalas] = useState<Sala[]>([]);
  const [salaActiva, setSalaActiva] = useState<string | null>(null);

  const [estado, setEstado] = useState<EstadoSala | null>(null);
  const [curvas, setCurvas] = useState<PuntoCurva[]>([]);
  const [historial, setHistorial] = useState<IndicadorDia[]>([]);

  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actualizado, setActualizado] = useState<Date | null>(null);

  const salirDeSesion = useCallback(() => {
    borrarToken();
    setAutenticado(false);
    setEstado(null);
    setCurvas([]);
    setHistorial([]);
  }, []);

  // Catalogo de salas: se pide una vez al entrar.
  useEffect(() => {
    if (!autenticado) return;
    let vigente = true;

    obtenerSalas()
      .then((lista) => {
        if (!vigente) return;
        setSalas(lista);
        setSalaActiva((previa) => previa ?? lista[0]?.codigo ?? null);
        if (lista.length === 0) setCargando(false);
      })
      .catch((excepcion) => {
        if (!vigente) return;
        if (excepcion instanceof ErrorApi && excepcion.estado === 401) {
          salirDeSesion();
        } else {
          setError(
            excepcion instanceof Error ? excepcion.message : "Error desconocido",
          );
        }
        setCargando(false);
      });

    return () => {
      vigente = false;
    };
  }, [autenticado, salirDeSesion]);

  // Datos de la sala activa, con refresco periodico.
  useEffect(() => {
    if (!autenticado || !salaActiva) return;
    let vigente = true;

    async function traer() {
      const sala = salaActiva as string;
      const hoy = hoyOperativo();
      try {
        const [nuevoEstado, nuevasCurvas, indicadores] = await Promise.all([
          obtenerEstado(sala, hoy),
          obtenerCurvas(sala, hoy, 5),
          obtenerIndicadores(sala, restarDias(hoy, DIAS_HISTORIAL - 1), hoy),
        ]);
        if (!vigente) return;
        setEstado(nuevoEstado);
        setCurvas(nuevasCurvas.puntos);
        setHistorial(indicadores.filas);
        setError(null);
        setActualizado(new Date());
      } catch (excepcion) {
        if (!vigente) return;
        if (excepcion instanceof ErrorApi && excepcion.estado === 401) {
          salirDeSesion();
          return;
        }
        setError(
          excepcion instanceof Error ? excepcion.message : "Error desconocido",
        );
      } finally {
        if (vigente) setCargando(false);
      }
    }

    setCargando(true);
    traer();
    const temporizador = setInterval(traer, INTERVALO_REFRESCO_MS);

    return () => {
      vigente = false;
      clearInterval(temporizador);
    };
  }, [autenticado, salaActiva, salirDeSesion]);

  if (!autenticado) {
    return <Ingreso alEntrar={() => setAutenticado(true)} />;
  }

  const sala = salas.find((item) => item.codigo === salaActiva);

  return (
    <>
      <header className="barra">
        <div className="barra__interior">
          <div>
            <h1 className="barra__titulo">
              Tiempo de espera · Consultorios externos
            </h1>
            <span className="barra__marco-legal">
              PLAN CERO COLAS · RM N.° 811-2018/MINSA
            </span>
          </div>

          {salas.length > 0 && (
            <div className="selector" role="group" aria-label="Establecimiento">
              {salas.map((item) => (
                <button
                  key={item.codigo}
                  className="selector__opcion"
                  aria-pressed={item.codigo === salaActiva}
                  onClick={() => setSalaActiva(item.codigo)}
                >
                  {item.establecimiento.replace("Hospital ", "")}
                </button>
              ))}
            </div>
          )}

          <div className="barra__derecha">
            <button className="boton boton--tenue" onClick={salirDeSesion}>
              Salir
            </button>
          </div>
        </div>
      </header>

      <main className="marco">
        {error && (
          <div className="tarjeta aviso aviso--error" style={{ marginTop: 24 }}>
            <p className="aviso__titulo">No se pudieron traer los datos</p>
            <p className="aviso__texto">{error}</p>
          </div>
        )}

        {cargando && !estado ? (
          <div style={{ marginTop: 32 }}>
            <div className="esqueleto" style={{ height: 108, marginBottom: 24 }} />
            <div className="esqueleto" style={{ height: 380 }} />
          </div>
        ) : estado ? (
          <>
            <section className="seccion">
              <div className="seccion__rotulo">
                <h2 className="seccion__titulo">
                  {formatearFechaLarga(estado.fecha_operativa)}
                </h2>
                <span className="seccion__nota">
                  {estado.muestras} mediciones en la jornada
                </span>
              </div>
              <Cifras estado={estado} />
            </section>

            <section className="seccion">
              <div className="seccion__rotulo">
                <h2 className="seccion__titulo">Cómo se formó la cola hoy</h2>
                <span className="seccion__nota">
                  La distancia entre curvas es la medición
                </span>
              </div>
              <div className="tarjeta">
                <div className="leyenda">
                  <span className="leyenda__item">
                    <span
                      className="leyenda__trazo"
                      style={{ background: "#0e7c66" }}
                    />
                    Llegadas acumuladas
                  </span>
                  <span className="leyenda__item">
                    <span
                      className="leyenda__trazo"
                      style={{ background: "#4b3fa8" }}
                    />
                    Pasos a consultorio
                  </span>
                  <span className="leyenda__formula">
                    L(t) = N_in − N_out · corrección {estado.ratio_acompanante}
                  </span>
                </div>
                <CurvasFlujo
                  puntos={curvas}
                  ratioAcompanante={estado.ratio_acompanante}
                />
              </div>
            </section>

            <section className="seccion">
              <div className="seccion__rotulo">
                <h2 className="seccion__titulo">Últimas {DIAS_HISTORIAL} jornadas</h2>
                <span className="seccion__nota">
                  Cada barra es la espera típica de un día
                </span>
              </div>
              <div className="tarjeta">
                <Historial filas={historial} />
              </div>
            </section>

            {historial.length > 0 && (
              <section className="seccion">
                <div className="seccion__rotulo">
                  <h2 className="seccion__titulo">Detalle por jornada</h2>
                </div>
                <div className="tarjeta" style={{ overflowX: "auto" }}>
                  <TablaJornadas filas={historial} />
                </div>
              </section>
            )}

            <footer className="pie">
              <span>
                Umbrales provisionales: hasta {UMBRALES.adecuado} min adecuado,
                hasta {UMBRALES.largo} min largo. Los fija la institución.
              </span>
              <span className="pie__mono">
                {sala?.codigo}
                {actualizado &&
                  ` · actualizado ${actualizado.toLocaleTimeString("es-PE", {
                    hour: "2-digit",
                    minute: "2-digit",
                    second: "2-digit",
                    hour12: false,
                  })}`}
              </span>
            </footer>
          </>
        ) : (
          !error && (
            <div className="tarjeta aviso" style={{ marginTop: 32 }}>
              <p className="aviso__titulo">No hay salas configuradas</p>
              <p className="aviso__texto">
                Ejecuta <code>make sembrar</code> en el backend para crear los
                establecimientos, las salas y sus líneas de conteo.
              </p>
            </div>
          )
        )}
      </main>
    </>
  );
}

/* Cliente de la API. Un solo lugar donde vive la forma de las respuestas,
 * para que un cambio en el contrato del backend rompa la compilacion en vez de
 * romperse en silencio en pantalla.
 */

export interface Sala {
  codigo: string;
  nombre: string;
  establecimiento: string;
  aforo_referencial: number | null;
  ratio_acompanante: number;
  activo: boolean;
}

export interface EstadoSala {
  sala_id: string;
  nombre: string;
  establecimiento: string;
  fecha_operativa: string;
  momento: string;
  ocupacion: number;
  n_entradas: number;
  n_salidas: number;
  espera_estimada_min: number | null;
  espera_p50_min: number | null;
  espera_p90_min: number | null;
  ratio_acompanante: number;
  muestras: number;
}

export interface PuntoCurva {
  t: string;
  n_in: number;
  n_out: number;
  ocupacion: number;
}

export interface RespuestaCurvas {
  sala_id: string;
  fecha_operativa: string;
  paso_min: number;
  puntos: PuntoCurva[];
}

export interface IndicadorDia {
  sala_id: string;
  fecha_operativa: string;
  n_pacientes: number;
  espera_p50_min: number | null;
  espera_p90_min: number | null;
  espera_promedio_min: number | null;
  ocupacion_maxima: number;
  descartados: number;
}

export interface RespuestaIndicadores {
  desde: string;
  hasta: string;
  filas: IndicadorDia[];
  resumen_p50_min: number | null;
  resumen_p90_min: number | null;
}

const BASE = import.meta.env.VITE_API_URL ?? "";
const LLAVE_SESION = "cerocolas.token";

export class ErrorApi extends Error {
  constructor(
    mensaje: string,
    readonly estado: number,
  ) {
    super(mensaje);
  }
}

export function leerToken(): string | null {
  return sessionStorage.getItem(LLAVE_SESION);
}

export function guardarToken(token: string): void {
  sessionStorage.setItem(LLAVE_SESION, token);
}

export function borrarToken(): void {
  sessionStorage.removeItem(LLAVE_SESION);
}

async function pedir<T>(ruta: string): Promise<T> {
  const token = leerToken();
  const respuesta = await fetch(`${BASE}${ruta}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });

  if (respuesta.status === 401) {
    borrarToken();
    throw new ErrorApi("La sesión expiró. Vuelve a ingresar.", 401);
  }
  if (respuesta.status === 403) {
    throw new ErrorApi("Esta cuenta no tiene permiso para ver indicadores.", 403);
  }
  if (!respuesta.ok) {
    throw new ErrorApi(
      `El servidor respondió ${respuesta.status}. Revisa que la API esté activa.`,
      respuesta.status,
    );
  }
  return (await respuesta.json()) as T;
}

export async function ingresar(email: string, password: string): Promise<void> {
  const respuesta = await fetch(`${BASE}/v1/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });

  if (respuesta.status === 401) {
    throw new ErrorApi("Correo o clave incorrectos.", 401);
  }
  if (!respuesta.ok) {
    throw new ErrorApi(
      "No se pudo conectar con la API. Verifica que esté ejecutándose.",
      respuesta.status,
    );
  }

  const datos = (await respuesta.json()) as { access_token: string };
  guardarToken(datos.access_token);
}

export const obtenerSalas = () => pedir<Sala[]>("/v1/catalogos/salas");

export const obtenerEstado = (sala: string, fecha?: string) =>
  pedir<EstadoSala>(`/v1/salas/${sala}/estado${fecha ? `?fecha=${fecha}` : ""}`);

export const obtenerCurvas = (sala: string, fecha?: string, pasoMin = 5) =>
  pedir<RespuestaCurvas>(
    `/v1/salas/${sala}/curvas?paso_min=${pasoMin}${fecha ? `&fecha=${fecha}` : ""}`,
  );

export const obtenerIndicadores = (sala: string, desde: string, hasta: string) =>
  pedir<RespuestaIndicadores>(
    `/v1/indicadores?sala=${sala}&desde=${desde}&hasta=${hasta}`,
  );

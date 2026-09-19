/* Clasificacion de la espera.
 *
 * Los tres niveles no se inventaron para este tablero: son la escala con que
 * el formulario del Plan Cero Colas ya califica la percepcion del usuario
 * (ADECUADO / LARGO / MUY LARGO). Reutilizarla evita introducir un vocabulario
 * paralelo que el personal tendria que aprender.
 *
 * Los umbrales en minutos, en cambio, SI son una decision del proyecto: no hay
 * cifra oficial publicada para sala de espera de consultorios externos. Se
 * anclan a la distribucion observada en la linea base de KoboCollect, donde la
 * mediana fue de 55 min en el Regional y 150 min en el Lorena. Un umbral de
 * ventanilla (del orden de 30 min) marcaria todas las jornadas de ambos
 * hospitales como MUY LARGO, y un indicador que nunca cambia no informa nada.
 * Con 60 y 120 minutos la escala distingue entre los dos establecimientos y
 * entre jornadas buenas y malas del mismo, que es para lo que sirve.
 *
 * Fijarlos en firme corresponde a la institucion, no al equipo de desarrollo;
 * la interfaz lo dice explicitamente al pie.
 */

export type NivelEspera = "adecuado" | "largo" | "muy-largo";

export const UMBRALES = {
  adecuado: 60,
  largo: 120,
} as const;

export const ETIQUETAS: Record<NivelEspera, string> = {
  adecuado: "ADECUADO",
  largo: "LARGO",
  "muy-largo": "MUY LARGO",
};

export const COLORES: Record<NivelEspera, string> = {
  adecuado: "#0e7c66",
  largo: "#b45309",
  "muy-largo": "#a4161a",
};

export function clasificar(minutos: number | null): NivelEspera | null {
  if (minutos === null || Number.isNaN(minutos)) return null;
  if (minutos <= UMBRALES.adecuado) return "adecuado";
  if (minutos <= UMBRALES.largo) return "largo";
  return "muy-largo";
}

/** Convierte minutos a una forma legible: 150 se lee mejor como "2 h 30". */
export function formatearDuracion(minutos: number | null): string {
  if (minutos === null || Number.isNaN(minutos)) return "—";
  const redondeado = Math.round(minutos);
  if (redondeado < 60) return `${redondeado} min`;
  const horas = Math.floor(redondeado / 60);
  const resto = redondeado % 60;
  return resto === 0 ? `${horas} h` : `${horas} h ${String(resto).padStart(2, "0")}`;
}

export function formatearHora(iso: string): string {
  return new Date(iso).toLocaleTimeString("es-PE", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function formatearFecha(iso: string): string {
  // Se construye con componentes para evitar el corrimiento de un dia que
  // produce interpretar "2026-08-15" como UTC en zonas negativas.
  const [anio, mes, dia] = iso.split("-").map(Number);
  return new Date(anio, mes - 1, dia).toLocaleDateString("es-PE", {
    day: "2-digit",
    month: "short",
  });
}

export function formatearFechaLarga(iso: string): string {
  const [anio, mes, dia] = iso.split("-").map(Number);
  return new Date(anio, mes - 1, dia).toLocaleDateString("es-PE", {
    weekday: "long",
    day: "numeric",
    month: "long",
  });
}

export function hoyOperativo(): string {
  // La jornada corta a las 3 a.m., igual que en el backend: un cruce a las
  // 5:40 pertenece a la jornada de ese dia.
  const ahora = new Date();
  if (ahora.getHours() < 3) ahora.setDate(ahora.getDate() - 1);
  return [
    ahora.getFullYear(),
    String(ahora.getMonth() + 1).padStart(2, "0"),
    String(ahora.getDate()).padStart(2, "0"),
  ].join("-");
}

export function restarDias(iso: string, dias: number): string {
  const [anio, mes, dia] = iso.split("-").map(Number);
  const fecha = new Date(anio, mes - 1, dia);
  fecha.setDate(fecha.getDate() - dias);
  return [
    fecha.getFullYear(),
    String(fecha.getMonth() + 1).padStart(2, "0"),
    String(fecha.getDate()).padStart(2, "0"),
  ].join("-");
}

#!/usr/bin/env python3
"""
Generador de datos sinteticos — Sistema de medicion del tiempo de espera
en sala de espera de consultorios externos.

ADVERTENCIA: los datos producidos son SIMULADOS. No provienen de ningun
establecimiento real, no deben presentarse como mediciones ni mezclarse con la
base de KoboCollect. Su unico fin es desarrollar y probar el software antes del
despliegue en campo.

Simula:
  - Llegadas con proceso no homogeneo (pico de madrugada, cola hacia el mediodia).
  - Acompanantes que entran a la sala pero nunca cruzan a consultorio.
  - Consultorios como servidores paralelos con inicio tardio de la atencion.
  - Prioridad para una fraccion de pacientes (rompe el supuesto FIFO a proposito).
  - Errores del detector: cruces perdidos y duplicados.
  - Cortes de red que retrasan la llegada de eventos al servidor.

Produce:
  cruces_<sede>.csv          eventos crudos, tal como los enviaria el agente
  verdad_terreno_<sede>.csv  espera real de cada paciente (no observable en campo)
  lote_ejemplo.json          carga util de ejemplo para POST /v1/cruces:lote
  resumen_validacion.csv     comparacion entre espera real y espera estimada
"""

import csv
import json
import math
import os
import random
import uuid
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=-5))          # America/Lima
SEMILLA = 20260808
DIAS_HABILES = 24
FECHA_INICIO = datetime(2026, 8, 23, tzinfo=TZ)   # sabado; 24 jornadas terminan hoy

SEDES = {
    "LOR-CE-01": {
        "nombre": "Hospital Antonio Lorena — sala de espera CE",
        "objetivo_p50_min": 150,
        "consultorios": 6,
        "apertura_sala": 5.5,
        "inicio_atencion": 7.6,
        "cierre": 13.5,
        "pacientes_dia": (128, 152),
        "consulta_media_min": 14.0,
        "consulta_cv": 0.45,
    },
    "REG-CE-01": {
        "nombre": "Hospital Regional del Cusco — sala de espera CE",
        "objetivo_p50_min": 55,
        "consultorios": 10,
        "apertura_sala": 6.2,
        "inicio_atencion": 7.1,
        "cierre": 13.5,
        "pacientes_dia": (150, 180),
        "consulta_media_min": 13.0,
        "consulta_cv": 0.40,
    },
}

RATIO_ACOMPANANTE = 0.34
FRACCION_PRIORIDAD = 0.12
P_CRUCE_PERDIDO = 0.04
P_CRUCE_DUPLICADO = 0.012
P_CORTE_RED = 0.10


def perfil_llegadas(h, cfg):
    if h < cfg["apertura_sala"] or h > cfg["cierre"] - 1.0:
        return 0.0
    pico = math.exp(-((h - (cfg["apertura_sala"] + 0.8)) ** 2) / 0.6)
    meseta = 0.25 * math.exp(-((h - 9.5) ** 2) / 6.0)
    return pico + meseta


def hora_a_dt(fecha, h):
    return fecha + timedelta(hours=h)


def lognormal(media, cv, rng):
    sigma = math.sqrt(math.log(1 + cv ** 2))
    mu = math.log(media) - sigma ** 2 / 2
    return rng.lognormvariate(mu, sigma)


def simular_dia(cfg, fecha, rng):
    n_pacientes = rng.randint(*cfg["pacientes_dia"])
    paso = 1 / 120
    n_puntos = int((cfg["cierre"] - 1.0 - cfg["apertura_sala"]) / paso)
    grilla = [cfg["apertura_sala"] + i * paso for i in range(n_puntos)]
    pesos = [perfil_llegadas(h, cfg) for h in grilla]
    llegadas = sorted(rng.choices(grilla, weights=pesos, k=n_pacientes))

    pacientes = [{
        "orden_llegada": i,
        "t_entrada": hora_a_dt(fecha, h) + timedelta(seconds=rng.uniform(0, 30)),
        "prioridad": rng.random() < FRACCION_PRIORIDAD,
        "acompanantes": 1 if rng.random() < RATIO_ACOMPANANTE else 0,
    } for i, h in enumerate(llegadas)]

    libre_desde = [hora_a_dt(fecha, cfg["inicio_atencion"]) for _ in range(cfg["consultorios"])]
    cierre_dt = hora_a_dt(fecha, cfg["cierre"])
    pendientes = sorted(pacientes, key=lambda p: p["t_entrada"])
    atendidos = []

    while pendientes:
        idx = min(range(len(libre_desde)), key=lambda k: libre_desde[k])
        t_disp = libre_desde[idx]
        presentes = [p for p in pendientes if p["t_entrada"] <= t_disp]
        if not presentes:
            libre_desde[idx] = pendientes[0]["t_entrada"]
            continue
        presentes.sort(key=lambda p: (not p["prioridad"], p["t_entrada"]))
        elegido = presentes[0]
        pendientes.remove(elegido)

        t_salida = max(t_disp, elegido["t_entrada"])
        if t_salida > cierre_dt:
            elegido["t_salida"] = None
            elegido["consultorio"] = None
            atendidos.append(elegido)
            continue
        dur = lognormal(cfg["consulta_media_min"], cfg["consulta_cv"], rng)
        libre_desde[idx] = t_salida + timedelta(minutes=dur)
        elegido["t_salida"] = t_salida
        elegido["consultorio"] = idx + 1
        atendidos.append(elegido)

    eventos = []
    for p in atendidos:
        eventos.append({"linea": "ENTRADA_SALA", "direccion": "IN", "t": p["t_entrada"]})
        for _ in range(p["acompanantes"]):
            eventos.append({"linea": "ENTRADA_SALA", "direccion": "IN",
                            "t": p["t_entrada"] + timedelta(seconds=rng.uniform(1, 15))})
        if p["t_salida"]:
            eventos.append({"linea": "PUERTA_CONS_%02d" % p["consultorio"],
                            "direccion": "OUT", "t": p["t_salida"]})
    eventos.sort(key=lambda e: e["t"])

    verdad = [{
        "fecha": fecha.date().isoformat(),
        "orden_llegada": p["orden_llegada"],
        "t_entrada": p["t_entrada"].isoformat(),
        "t_salida": p["t_salida"].isoformat() if p["t_salida"] else "",
        "espera_min": round((p["t_salida"] - p["t_entrada"]).total_seconds() / 60, 2)
                      if p["t_salida"] else "",
        "prioridad": int(p["prioridad"]),
        "acompanantes": p["acompanantes"],
        "consultorio": p["consultorio"] or "",
    } for p in atendidos]

    return eventos, verdad


def aplicar_ruido_detector(eventos, rng):
    cortes = {f for f in range(48) if rng.random() < P_CORTE_RED}
    lote = str(uuid.uuid4())
    salida = []
    for e in eventos:
        if rng.random() < P_CRUCE_PERDIDO:
            continue
        veces = 2 if rng.random() < P_CRUCE_DUPLICADO else 1
        franja = int((e["t"].hour * 60 + e["t"].minute) / 30)
        retraso = timedelta(minutes=rng.uniform(8, 25)) if franja in cortes else timedelta(0)
        for _ in range(veces):
            salida.append({
                "lote_id": lote,
                "linea": e["linea"],
                "direccion": e["direccion"],
                "ocurrido_en": e["t"].isoformat(),
                "recibido_en": (e["t"] + retraso + timedelta(seconds=rng.uniform(0.5, 3))).isoformat(),
                "confianza": round(min(0.99, max(0.55, rng.gauss(0.87, 0.07))), 3),
                "track_id_local": rng.randint(1, 9999),
            })
        if rng.random() < 0.02:
            lote = str(uuid.uuid4())
    return salida


def estimar_por_curvas(cruces, ratio_acompanante=0.0):
    """Reconstruye la espera desde las curvas acumuladas, como hara la API."""
    entradas = sorted(datetime.fromisoformat(c["ocurrido_en"])
                      for c in cruces if c["direccion"] == "IN")
    salidas = sorted(datetime.fromisoformat(c["ocurrido_en"])
                     for c in cruces if c["direccion"] == "OUT")
    factor = 1.0 + ratio_acompanante
    esperas = []
    for k, t_out in enumerate(salidas):
        idx = int(round(k * factor))
        if idx >= len(entradas):
            break
        w = (t_out - entradas[idx]).total_seconds() / 60
        if w >= 0:
            esperas.append(w)
    return esperas


def percentil(v, q):
    if not v:
        return float("nan")
    v = sorted(v)
    pos = (len(v) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return v[int(lo)] if lo == hi else v[int(lo)] + (v[int(hi)] - v[int(lo)]) * (pos - lo)


def main():
    rng = random.Random(SEMILLA)
    salida_dir = os.path.dirname(os.path.abspath(__file__))
    resumen, ejemplo = [], None

    for sede_id, cfg in SEDES.items():
        cruces_all, verdad_all = [], []
        fecha, dias = FECHA_INICIO, 0
        while dias < DIAS_HABILES:
            if fecha.weekday() < 6:
                eventos, verdad = simular_dia(cfg, fecha, rng)
                cruces = aplicar_ruido_detector(eventos, rng)
                for c in cruces:
                    c["sala_id"] = sede_id
                cruces_all.extend(cruces)
                verdad_all.extend(verdad)

                reales = [v["espera_min"] for v in verdad if v["espera_min"] != ""]
                ing = estimar_por_curvas(cruces)
                cor = estimar_por_curvas(cruces, RATIO_ACOMPANANTE)
                resumen.append({
                    "sala_id": sede_id,
                    "fecha": fecha.date().isoformat(),
                    "n_pacientes": len(reales),
                    "p50_real": round(percentil(reales, 0.50), 1),
                    "p90_real": round(percentil(reales, 0.90), 1),
                    "p50_estimado_ingenuo": round(percentil(ing, 0.50), 1),
                    "p50_estimado_corregido": round(percentil(cor, 0.50), 1),
                    "p90_estimado_corregido": round(percentil(cor, 0.90), 1),
                    "error_p50_min": round(percentil(cor, 0.50) - percentil(reales, 0.50), 1),
                    "error_p90_min": round(percentil(cor, 0.90) - percentil(reales, 0.90), 1),
                })
                dias += 1
            fecha += timedelta(days=1)

        with open(os.path.join(salida_dir, "cruces_%s.csv" % sede_id), "w",
                  newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["sala_id", "lote_id", "linea", "direccion",
                                              "ocurrido_en", "recibido_en", "confianza",
                                              "track_id_local"])
            w.writeheader()
            w.writerows(cruces_all)

        with open(os.path.join(salida_dir, "verdad_terreno_%s.csv" % sede_id), "w",
                  newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(verdad_all[0].keys()))
            w.writeheader()
            w.writerows(verdad_all)

        if ejemplo is None:
            ejemplo = {
                "sala_id": sede_id,
                "agente": "lorena-edge-01",
                "cruces": [{"linea": c["linea"], "direccion": c["direccion"],
                            "ocurrido_en": c["ocurrido_en"], "confianza": c["confianza"]}
                           for c in cruces_all[:6]],
            }

        print("%s: %6d cruces, %5d pacientes" % (sede_id, len(cruces_all), len(verdad_all)))

    with open(os.path.join(salida_dir, "lote_ejemplo.json"), "w", encoding="utf-8") as f:
        json.dump(ejemplo, f, ensure_ascii=False, indent=2)

    with open(os.path.join(salida_dir, "resumen_validacion.csv"), "w",
              newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(resumen[0].keys()))
        w.writeheader()
        w.writerows(resumen)

    print("\n%-12s %9s %9s %10s %10s %9s" %
          ("Sede", "objetivo", "P50 real", "ingenuo", "corregido", "EAM"))
    for sede_id in SEDES:
        f_ = [r for r in resumen if r["sala_id"] == sede_id]
        n = len(f_)
        print("%-12s %9d %9.1f %10.1f %10.1f %9.1f" % (
            sede_id,
            SEDES[sede_id]["objetivo_p50_min"],
            sum(r["p50_real"] for r in f_) / n,
            sum(r["p50_estimado_ingenuo"] for r in f_) / n,
            sum(r["p50_estimado_corregido"] for r in f_) / n,
            sum(abs(r["error_p50_min"]) for r in f_) / n))
    print("\nEAM = error absoluto medio de la mediana diaria, en minutos.")
    print("ADVERTENCIA: datos ficticios, solo para desarrollo y pruebas.")


if __name__ == "__main__":
    main()

-- Consultas de referencia sobre PostgreSQL para el Sistema Cero Colas.
-- Acompanan al documento "PostgreSQL como motor de persistencia".
-- Verificadas contra el estimador en Python: diferencia de 1e-6 minutos.

-- ---------------------------------------------------------------------------
-- 1. Curvas de flujo acumulado y percentiles de una jornada
-- ---------------------------------------------------------------------------
-- Se numeran entradas y salidas por separado y se emparejan por posicion
-- aplicando el factor de correccion de la sala. Nadie es identificado: la
-- correspondencia es entre posiciones de dos curvas, no entre personas.
WITH entradas AS (
  SELECT ocurrido_en AS t,
         ROW_NUMBER() OVER (ORDER BY ocurrido_en) - 1 AS n_in
  FROM cruce
  WHERE sala_id = :sala
    AND fecha_operativa = :fecha
    AND ocurrido_en >= :inicio_jornada   -- habilita la exclusion de particiones
    AND ocurrido_en <  :fin_jornada
    AND direccion = 'IN'
),
salidas AS (
  SELECT ocurrido_en AS t,
         ROW_NUMBER() OVER (ORDER BY ocurrido_en) - 1 AS k
  FROM cruce
  WHERE sala_id = :sala
    AND fecha_operativa = :fecha
    AND ocurrido_en >= :inicio_jornada
    AND ocurrido_en <  :fin_jornada
    AND direccion = 'OUT'
),
pares AS (
  SELECT s.k,
         e.t AS t_entrada,
         s.t AS t_salida,
         EXTRACT(EPOCH FROM (s.t - e.t)) / 60.0 AS espera_min
  FROM salidas s
  JOIN entradas e ON e.n_in = ROUND(s.k * (1 + :factor))::int
)
SELECT
  COUNT(*)                                               AS muestras,
  PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY espera_min) AS p50,
  PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY espera_min) AS p90
FROM pares
WHERE espera_min BETWEEN 0 AND :espera_maxima;

-- ---------------------------------------------------------------------------
-- 2. Ocupacion instantanea a lo largo de la jornada
-- ---------------------------------------------------------------------------
-- L(t) = N_in(t) - N_out(t). La suma acumulada con signo evita tener que
-- unir dos series y recorrerlas en paralelo.
SELECT ocurrido_en,
       SUM(CASE WHEN direccion = 'IN' THEN 1 ELSE -1 END)
         OVER (ORDER BY ocurrido_en ROWS UNBOUNDED PRECEDING) AS ocupacion
FROM cruce
WHERE sala_id = :sala AND fecha_operativa = :fecha
ORDER BY ocurrido_en;

-- ---------------------------------------------------------------------------
-- 3. Fecha operativa: la jornada corta a las 3 de la madrugada
-- ---------------------------------------------------------------------------
-- Las colas empiezan antes del amanecer; cortar a medianoche partiria en dos
-- una jornada real.
SELECT (ocurrido_en AT TIME ZONE 'America/Lima' - INTERVAL '3 hours')::date
         AS fecha_operativa,
       COUNT(*) FILTER (WHERE direccion = 'IN')  AS entradas,
       COUNT(*) FILTER (WHERE direccion = 'OUT') AS salidas
FROM cruce
WHERE sala_id = :sala
GROUP BY 1 ORDER BY 1 DESC LIMIT 30;

-- ---------------------------------------------------------------------------
-- 4. Ingesta idempotente en una sola sentencia atomica
-- ---------------------------------------------------------------------------
-- La garantia la impone la restriccion UNIQUE, no una consulta previa:
-- consultar y despues insertar deja una ventana bajo concurrencia.
INSERT INTO lote_ingesta (id, agente_id, idempotency_key, recibido_en, n_eventos)
VALUES (:id, :agente, :clave, now(), :n)
ON CONFLICT (idempotency_key) DO NOTHING
RETURNING id;   -- sin filas => el lote ya se habia procesado

-- ---------------------------------------------------------------------------
-- 5. Creacion automatica de particiones mensuales
-- ---------------------------------------------------------------------------
-- Se crean por adelantado: una particion creada en la primera insercion del
-- mes convertiria esa insercion en una operacion de esquema, que toma
-- bloqueos y podria coincidir con el pico de las siete de la manana.
CREATE OR REPLACE FUNCTION crear_particion_cruce(mes date)
RETURNS void AS $$
DECLARE
  nombre text := 'cruce_' || to_char(mes, 'YYYY_MM');
  inicio date := date_trunc('month', mes);
  fin    date := (date_trunc('month', mes) + interval '1 month')::date;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = nombre) THEN
    EXECUTE format(
      'CREATE TABLE %I PARTITION OF cruce FOR VALUES FROM (%L) TO (%L)',
      nombre, inicio, fin);
  END IF;
END;
$$ LANGUAGE plpgsql;

SELECT crear_particion_cruce(
         (date_trunc('month', now()) + (n || ' month')::interval)::date)
FROM generate_series(-2, 12) AS n;

-- ---------------------------------------------------------------------------
-- 6. Diagnostico operativo
-- ---------------------------------------------------------------------------
-- Particiones existentes y su tamano.
SELECT c.relname AS particion,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS tamano
FROM pg_class c
JOIN pg_inherits i ON i.inhrelid = c.oid
JOIN pg_class p    ON p.oid = i.inhparent
WHERE p.relname = 'cruce'
ORDER BY c.relname;

-- Agentes que dejaron de reportar.
SELECT hostname, estado, ultimo_heartbeat,
       now() - ultimo_heartbeat AS silencio
FROM agente
ORDER BY ultimo_heartbeat NULLS FIRST;

-- Lineas sin cruces hoy: delata una camara caida o mal enfocada.
SELECT lv.nombre, COUNT(cr.id) AS cruces
FROM linea_virtual lv
LEFT JOIN cruce cr
       ON cr.linea_id = lv.id
      AND cr.fecha_operativa = CURRENT_DATE
GROUP BY lv.nombre
ORDER BY cruces;

-- Desfase de reloj de los equipos de borde. Un valor medio grande y estable
-- apunta al reloj; uno que crece y vuelve a cero, a un corte de red.
SELECT sala_id,
       AVG(EXTRACT(EPOCH FROM (recibido_en - ocurrido_en))) AS desfase_s,
       MAX(EXTRACT(EPOCH FROM (recibido_en - ocurrido_en))) AS maximo_s
FROM cruce
WHERE fecha_operativa >= CURRENT_DATE - 7
GROUP BY sala_id;

-- ---------------------------------------------------------------------------
-- 7. Verificacion de la exclusion de particiones
-- ---------------------------------------------------------------------------
-- En el plan debe aparecer UNA sola particion explorada. Si aparecen todas,
-- el filtro esta mal formulado y el particionado no aporta nada.
EXPLAIN (ANALYZE, BUFFERS)
SELECT direccion, ocurrido_en
FROM cruce
WHERE sala_id = :sala
  AND ocurrido_en >= '2026-08-15T03:00-05:00'
  AND ocurrido_en <  '2026-08-16T03:00-05:00';

-- ---------------------------------------------------------------------------
-- 8. Archivado de una particion antigua
-- ---------------------------------------------------------------------------
-- Retencion barata: desconectar y respaldar, no borrar masivamente.
ALTER TABLE cruce DETACH PARTITION cruce_2026_01;
-- pg_dump -t cruce_2026_01 -Fc -f cruce_2026_01.dump
-- DROP TABLE cruce_2026_01;

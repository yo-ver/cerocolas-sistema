# Construccion multietapa de la API.
#
# La etapa `pruebas` ejecuta la suite durante la propia construccion: si un
# test falla o la cobertura baja del umbral, la imagen no llega a generarse.
# Convierte la verificacion en precondicion del empaquetado y no en un paso
# paralelo que pudiera omitirse por accidente.
#
# La etapa `produccion` parte de nuevo de la imagen oficial minima y copia
# unicamente el codigo, de modo que el artefacto distribuido no contiene ni
# pruebas ni herramientas de desarrollo.

# --- base -------------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=America/Lima

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY scripts/ ./scripts/

# --- pruebas ----------------------------------------------------------------
FROM base AS pruebas

COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY tests/ ./tests/

# La suite corre contra SQLite: no hace falta un PostgreSQL durante la
# construccion, y el comportamiento verificado es el mismo porque el acceso a
# datos pasa integramente por SQLAlchemy.
ENV DATABASE_URL="sqlite+pysqlite:///./construccion.db" \
    JWT_SECRETO="secreto-de-construccion"

RUN ruff check app scripts tests \
 && pytest --cov=app --cov-branch --cov-report=term-missing --cov-fail-under=90 tests/

CMD ["pytest", "-v", "tests/"]

# --- produccion -------------------------------------------------------------
FROM python:3.12-slim AS produccion

LABEL org.opencontainers.image.title="Cero Colas - API de medicion de tiempo de espera" \
      org.opencontainers.image.description="Mide el tiempo de espera en sala de espera de consultorios externos mediante curvas de flujo acumulado" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later" \
      org.opencontainers.image.vendor="UNSAAC - Escuela de Posgrado"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=America/Lima

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY scripts/ ./scripts/

# Usuario sin privilegios: las imagenes oficiales corren como administrador
# por defecto y no hay ninguna razon para que esta API lo haga.
RUN useradd --create-home --uid 10001 aplicacion && chown -R aplicacion:aplicacion /app
USER aplicacion

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/salud || exit 1

# Render y otros PaaS inyectan el puerto por variable de entorno.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

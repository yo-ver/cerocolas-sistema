.PHONY: instalar sembrar servir pruebas cargar limpiar openapi lint

instalar:
	cd backend && pip install -r requirements-dev.txt

sembrar:
	cd backend && python -m scripts.sembrar

servir:
	cd backend && uvicorn app.main:app --reload --port 8000

pruebas:
	cd backend && python -m pytest tests/ -v

cargar:          ## Carga datos SINTETICOS contra la API local
	cd backend && python -m scripts.cargar_sinteticos --api http://127.0.0.1:8000

generar-datos:
	cd datos_sinteticos && python generador_datos_sinteticos.py

openapi:
	cd backend && python -c "import json; from app.main import app; \
	print(json.dumps(app.openapi(), indent=2, ensure_ascii=False))" > ../openapi.json
	@echo "escrito en openapi.json"

lint:
	cd backend && ruff check app scripts tests

limpiar:
	rm -f backend/cerocolas.db
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

validar:         ## Compara la API contra la verdad de terreno sintetica
	cd backend && python -m scripts.validar

ajustar-ratio:   ## Ajusta el factor de correccion de cada sala
	cd backend && python -m scripts.ajustar_ratio --aplicar

tablero:         ## Servidor de desarrollo del tablero
	cd tablero && npm run dev

tablero-build:   ## Compila el tablero para produccion
	cd tablero && npm run build

# --- verificacion y CI/CD ---------------------------------------------------

lint-todo:       ## ruff sobre backend y agente
	cd backend && ruff check app scripts tests && ruff format --check .
	cd agente  && ruff check . && ruff format --check .

pruebas-unitarias:
	cd backend && pytest -m unitaria -v
	cd agente  && pytest -m unitaria -v

pruebas-integracion:
	cd backend && pytest -m integracion -v
	cd agente  && pytest -m integracion -v

cobertura:       ## Suite completa con cobertura de ramas e informe HTML
	cd backend && pytest --cov=app --cov-branch --cov-report=term-missing \
		--cov-report=html --cov-fail-under=90 tests/
	cd agente  && pytest --cov=agente --cov-branch --cov-report=term-missing \
		--cov-fail-under=80 tests/

verificar: lint-todo cobertura tablero-build  ## Todo lo que corre la canalizacion

docker-pruebas:  ## Ejecuta la suite dentro de la imagen
	docker build --target pruebas -t cerocolas-api:test ./backend

docker-produccion:
	docker build --target produccion -t cerocolas-api:latest ./backend

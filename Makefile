# Day-to-day commands for the Compose stack. `make` lists them.
COMPOSE := docker compose
URL := http://localhost:8000
BACKUPS := backups
# Dev mode runs the API and UI on this machine against the Compose database.
DB_PORT := $(if $(LATTICE_DB_PORT),$(LATTICE_DB_PORT),5432)
MODEL_CACHE := $(CURDIR)/.cache/fastembed
MODEL := BAAI/bge-small-en-v1.5
DEV_ENV := LATTICE_INSECURE_DEV_COOKIES=true LATTICE_PUBLIC_ORIGIN=http://localhost:5173 \
	FASTEMBED_CACHE_PATH=$(MODEL_CACHE) \
	LATTICE_DATABASE_URL=postgresql+psycopg://lattice_app:lattice_app_dev@localhost:$(DB_PORT)/lattice \
	LATTICE_MIGRATION_DATABASE_URL=postgresql+psycopg://lattice_owner:lattice_owner_dev@localhost:$(DB_PORT)/lattice

.DEFAULT_GOAL := help
.PHONY: help build start dev stop restart down ps logs seed migrate psql backup restore test lint e2e

help: ## List the commands
	@grep -E '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  make %-9s %s\n", $$1, $$2}'

build: ## Build the app and migrate images
	$(COMPOSE) build

# Always rebuilds: a plain `docker compose start` reuses old images and serves stale code.
start: ## Build if needed, migrate, and start the stack
	$(COMPOSE) up -d --build
	@printf 'Waiting for %s ' "$(URL)"; for i in $$(seq 1 60); do curl -sf $(URL)/api/health >/dev/null && { echo ok; exit 0; }; printf .; sleep 1; done; echo; echo 'Not healthy after 60s; run make logs'; exit 1

# The app container is stopped so the local API can take port 8000; `make start` switches back.
dev: ## Auto-reloading API and UI on this machine at http://localhost:5173 (Ctrl-C stops both)
	$(COMPOSE) stop app
	$(COMPOSE) up -d --wait db
	cd backend && $(DEV_ENV) uv run alembic upgrade head
	@test -d $(MODEL_CACHE) || { echo 'Downloading the embedding model once into .cache/fastembed'; \
		cd backend && FASTEMBED_CACHE_PATH=$(MODEL_CACHE) uv run python -c "from fastembed import TextEmbedding; TextEmbedding('$(MODEL)')"; }
	@test -d frontend/node_modules || (cd frontend && npm ci)
	@echo 'UI http://localhost:5173 (API :8000, both reload on save). Ctrl-C stops both; make start goes back to Docker.'
	@trap 'kill $$(jobs -p) 2>/dev/null' INT TERM; \
		(cd backend && $(DEV_ENV) uv run uvicorn --factory app.main:create_app --reload --reload-dir app --port 8000) & \
		(cd frontend && npm run dev -- --port 5173 --strictPort) & \
		wait

stop: ## Stop the containers (data is kept)
	$(COMPOSE) stop

restart: stop start ## Stop, rebuild if needed, and start

down: ## Remove the containers (the database volume is kept)
	$(COMPOSE) down

ps: ## Show container status
	$(COMPOSE) ps -a

logs: ## Follow logs; one service with s=app, s=db or s=migrate
	$(COMPOSE) logs -f --tail=100 $(s)

seed: ## Load the demo workspace and people
	$(COMPOSE) exec app python -m app.seed

migrate: ## Run database migrations
	$(COMPOSE) run --rm migrate

psql: ## Open psql on the lattice database as postgres
	$(COMPOSE) exec db psql -U postgres -d lattice

backup: ## Dump the database to backups/lattice-<time>.sql
	@mkdir -p $(BACKUPS)
	$(COMPOSE) exec -T db pg_dump -U postgres -d lattice --clean --if-exists > $(BACKUPS)/lattice-$$(date +%Y%m%d-%H%M%S).sql
	@ls -t $(BACKUPS)/*.sql | head -1

restore: ## Replace the database with a dump: make restore file=backups/<name>.sql
	@test -n "$(file)" -a -f "$(file)" || { echo 'Usage: make restore file=backups/<name>.sql'; exit 1; }
	$(COMPOSE) exec -T db psql -U postgres -d lattice -v ON_ERROR_STOP=1 -q < $(file) > /dev/null

test: ## Backend fast suite and frontend unit tests
	cd backend && uv run pytest -m "not property and not benchmark_model" -q
	cd frontend && npm test

lint: ## Ruff, frontend lint and type-checked build
	cd backend && uv run ruff check .
	cd frontend && npm run lint && npm run build

e2e: ## Browser tests against the running stack (make start && make seed first)
	cd frontend && npm run e2e

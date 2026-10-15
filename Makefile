# Day-to-day commands for the Compose stack. `make` lists them.
COMPOSE := docker compose
URL := http://localhost:8000
BACKUPS := backups

.DEFAULT_GOAL := help
.PHONY: help build start stop restart down ps logs seed migrate psql backup restore test lint e2e

help: ## List the commands
	@grep -E '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  make %-9s %s\n", $$1, $$2}'

build: ## Build the app and migrate images
	$(COMPOSE) build

# Always rebuilds: a plain `docker compose start` reuses old images and serves stale code.
start: ## Build if needed, migrate, and start the stack
	$(COMPOSE) up -d --build
	@printf 'Waiting for %s ' "$(URL)"; for i in $$(seq 1 60); do curl -sf $(URL)/api/health >/dev/null && { echo ok; exit 0; }; printf .; sleep 1; done; echo; echo 'Not healthy after 60s; run make logs'; exit 1

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

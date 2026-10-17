.PHONY: setup backend frontend dev test lint build up down verify-models browser

setup: ## install backend + frontend dependencies
	cd backend && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
	cd frontend && npm install
	@test -f backend/.env || cp backend/.env.example backend/.env
	@test -f frontend/.env.local || cp frontend/.env.example frontend/.env.local
	@echo "Add at least one provider key to backend/.env, then run: make dev"

backend: ## run the API with auto-reload
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

frontend: ## run the Next.js dev server
	cd frontend && npm run dev

dev: ## run both (backend in background, frontend in foreground)
	@(cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000 &) ; cd frontend && npm run dev

test: ## backend unit + API tests
	cd backend && .venv/bin/python -m pytest -q

verify-models: ## check config/models.yaml against what each provider serves
	cd backend && .venv/bin/python scripts_verify_models.py

browser: ## install the optional headless-browser fallback
	cd backend && .venv/bin/pip install -r requirements-optional.txt && .venv/bin/playwright install chromium
	@echo "Now set BROWSER_FALLBACK_ENABLED=true in backend/.env" 

lint: ## ruff + eslint + tsc
	cd backend && .venv/bin/ruff check app tests
	cd frontend && npx next lint && npx tsc --noEmit

build: ## production build of the frontend
	cd frontend && npm run build

up: ## docker compose up
	docker compose up --build

down:
	docker compose down

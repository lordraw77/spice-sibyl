.PHONY: up down logs backend frontend test-backend install-backend install-frontend

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f

backend:
	cd backend && uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm install && npm start

test-backend:
	cd backend && pytest

install-backend:
	cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

install-frontend:
	cd frontend && npm install

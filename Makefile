.PHONY: build up down test simulate loadtest local

build:
	docker build -t lb-server ./server
	docker compose build

up: build
	docker compose up -d
	@echo "Load balancer running on http://localhost:5000"

down:
	docker compose down

test:
	python -m pytest -v

simulate:
	python analysis/simulate.py

loadtest:
	python analysis/load_test.py

local:
	python -m lb.app --backend local
